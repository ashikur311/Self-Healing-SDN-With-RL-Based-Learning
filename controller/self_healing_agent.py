#!/usr/bin/env python3
"""
Integrated self-healing controller (Paper fix #1, sub-step 1.5).

This merges the two agents from the thesis into one closed loop that ACTUALLY
recovers traffic:

    Agent 1 (telemetry)  : polls OFPPortStats every 3s, computes normalized
                           state [throughput, loss, delay] per port.
    Agent 2 (PPO)        : the trained Stable-Baselines3 model decides
                           0 = do nothing, 1 = reroute.
    Actuator             : on PPO action==1 for a primary-path port that was
                           carrying traffic and has now failed, it installs the
                           backup path with real OFPFlowMod rules.

Difference vs controller/reroute_agent.py: that file triggers on the raw
link-down EVENT (a good baseline/ablation). THIS file lets the PPO DECISION pull
the trigger, matching the paper's narrative.

Loop-safety identical to reroute_agent.py: explicit proactive flows + static ARP
(set in the topology) => the physical loop causes no broadcast storm.

Idle-port guard: an unused port reads 0 throughput, which our zero-packet rule
maps to 100% loss -> PPO would say "reroute". So we only let PPO trigger on a
port that has ALREADY carried traffic (self.active_ports); a real fault is a
previously-active primary port dropping to zero.

Run:  /home/mininet/venvs/ryu-4.34-py39/bin/ryu-manager controller/self_healing_agent.py
"""
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
import time
import os
import numpy as np
from stable_baselines3 import PPO

H1_MAC = '00:00:00:00:00:01'
H3_MAC = '00:00:00:00:00:03'

# Port map (must match reroute_topo.py)
S1, S2, S3 = 1, 2, 3
S1_TO_H1, S1_TO_S2, S1_TO_S3 = 1, 2, 3
S2_TO_S1, S2_TO_S3 = 1, 2
S3_TO_H3, S3_TO_S2, S3_TO_S1 = 1, 2, 3

PRIMARY_PORTS = {(S1, S1_TO_S2), (S2, S2_TO_S1), (S2, S2_TO_S3), (S3, S3_TO_S2)}

PRIO_PRIMARY = 10
PRIO_BACKUP = 100

MODEL_PATH = "/home/mininet/self_healing_sdn/sdn_recovery_agent"
POLL_SECONDS = 3
ACTIVE_KBPS = 0.5           # a port carrying > this is considered "in use"
OF_LOCAL_PORT = 4294967294  # OFPP_LOCAL, skip in stats


class SelfHealingAgent(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SelfHealingAgent, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.on_backup = False
        self.port_stats = {}     # (dpid,port) -> (bytes, pkts, time)
        self.req_times = {}      # dpid -> request timestamp
        self.active_ports = set()  # primary ports that have carried real traffic

        self.logger.info("=========================================")
        if os.path.exists(MODEL_PATH + ".zip"):
            self.rl_agent = PPO.load(MODEL_PATH)
            self.logger.info("Agent 2 (PPO) loaded. Autonomous decision: ON")
        else:
            self.rl_agent = None
            self.logger.warning("PPO model NOT found -> reroute disabled")
        self.logger.info("=========================================")

        self.monitor_thread = hub.spawn(self._monitor)

    # ---- flow helpers (same mechanism as reroute_agent.py) -----------------
    def _add_flow(self, dp, priority, eth_dst, out_port):
        parser = dp.ofproto_parser
        match = parser.OFPMatch(eth_dst=eth_dst)
        actions = [parser.OFPActionOutput(out_port)]
        inst = [parser.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority,
                                      match=match, instructions=inst))

    def _install_primary(self, dp):
        did = dp.id
        if did == S1:
            self._add_flow(dp, PRIO_PRIMARY, H3_MAC, S1_TO_S2)
            self._add_flow(dp, PRIO_PRIMARY, H1_MAC, S1_TO_H1)
        elif did == S2:
            self._add_flow(dp, PRIO_PRIMARY, H3_MAC, S2_TO_S3)
            self._add_flow(dp, PRIO_PRIMARY, H1_MAC, S2_TO_S1)
        elif did == S3:
            self._add_flow(dp, PRIO_PRIMARY, H3_MAC, S3_TO_H3)
            self._add_flow(dp, PRIO_PRIMARY, H1_MAC, S3_TO_S2)

    def trigger_reroute(self, reason=""):
        if self.on_backup:
            return
        s1 = self.datapaths.get(S1)
        s3 = self.datapaths.get(S3)
        if not (s1 and s3):
            return
        self._add_flow(s1, PRIO_BACKUP, H3_MAC, S1_TO_S3)
        self._add_flow(s3, PRIO_BACKUP, H1_MAC, S3_TO_S1)
        self.on_backup = True
        self.logger.warning("[ACTUATE] installed BACKUP path s1-s3 (OFPFlowMod). %s", reason)

    def _del_flow(self, dp, priority, eth_dst):
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        dp.send_msg(parser.OFPFlowMod(datapath=dp, command=ofp.OFPFC_DELETE_STRICT,
                                      priority=priority, out_port=ofp.OFPP_ANY,
                                      out_group=ofp.OFPG_ANY,
                                      match=parser.OFPMatch(eth_dst=eth_dst)))

    def restore_primary(self):
        """Failback: drop backup flows when the primary link returns so the
        prio-10 primary path carries traffic again (enables repeat measurement)."""
        if not self.on_backup:
            return
        s1 = self.datapaths.get(S1)
        s3 = self.datapaths.get(S3)
        if not (s1 and s3):
            return
        self._del_flow(s1, PRIO_BACKUP, H3_MAC)
        self._del_flow(s3, PRIO_BACKUP, H1_MAC)
        self.on_backup = False
        self.logger.warning("[RESTORE] primary link back -> removed backup; primary path active")

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status(self, ev):
        # Detection is PPO-driven; this handler only performs failback on link-up.
        msg = ev.msg
        dp = msg.datapath
        if (dp.id, msg.desc.port_no) in PRIMARY_PORTS:
            if not (msg.desc.state & dp.ofproto.OFPPS_LINK_DOWN):
                self.restore_primary()

    # ---- Agent 1: telemetry loop -------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        self._install_primary(dp)
        self.logger.info("[INIT] s%s connected; primary flows installed", dp.id)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER and dp.id in self.datapaths:
            del self.datapaths[dp.id]

    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                self.req_times[dp.id] = time.time()
                parser = dp.ofproto_parser
                dp.send_msg(parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))
            hub.sleep(POLL_SECONDS)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _stats_reply(self, ev):
        dpid = ev.msg.datapath.id
        now = time.time()
        delay_ms = round((now - self.req_times.get(dpid, now)) * 1000, 2)

        for stat in ev.msg.body:
            if stat.port_no == OF_LOCAL_PORT:
                continue
            key = (dpid, stat.port_no)
            curr_bytes = stat.rx_bytes + stat.tx_bytes

            throughput_kbps = 0.0
            loss_rate = 0.0
            if key in self.port_stats:
                prev_bytes, _prev_pkts, prev_time = self.port_stats[key]
                dt = now - prev_time
                if dt > 0:
                    throughput_kbps = ((curr_bytes - prev_bytes) * 8) / (dt * 1000)
                    if throughput_kbps == 0.0:      # zero-packet rule = dead link
                        loss_rate = 1.0
            self.port_stats[key] = (curr_bytes, stat.rx_packets + stat.tx_packets, now)

            # We only make reroute decisions about primary-path ports.
            if key not in PRIMARY_PORTS:
                continue
            if throughput_kbps > ACTIVE_KBPS:
                self.active_ports.add(key)   # this port is genuinely carrying traffic

            if self.rl_agent is None or key not in self.active_ports:
                continue

            # ---- Agent 2: PPO decides ----
            state = np.array([min(throughput_kbps / 25000.0, 1.0),
                              min(loss_rate, 1.0),
                              min(delay_ms / 100.0, 1.0)], dtype=np.float32)
            action, _ = self.rl_agent.predict(state, deterministic=True)
            if int(action) == 1 and throughput_kbps == 0.0:
                self.logger.warning("[AGENT2] PPO action=1 (reroute) on failed primary "
                                    "port s%s:%s | state=%s", dpid, stat.port_no,
                                    np.round(state, 3).tolist())
                self.trigger_reroute(reason="triggered by PPO decision")
