#!/usr/bin/env python3
"""
Self-healing reroute controller (Paper fix #1).

This is the piece the peer review flagged as MISSING: the original
monitor_agent.py only PRINTS "rerouting" but never changes any flows. This
controller actually installs OpenFlow rules (OFPFlowMod) to steer the h1<->h3
traffic onto a backup path when the primary path fails.

Pairs with topology/reroute_topo.py (fixed ports + static ARP). Forwarding is
100% explicit proactive flows, so the physical loop causes NO broadcast storm.

Paths (see topology diagram):
    PRIMARY (priority 10):  h1<->h3 via s1 - s2 - s3
    BACKUP  (priority 100): h1<->h3 via the direct s1 - s3 link (bypasses s2)

Higher-priority backup flows override the primary flows the instant a primary
link goes down.

Run:  ryu-manager controller/reroute_agent.py
"""
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3

H1_MAC = '00:00:00:00:00:01'
H3_MAC = '00:00:00:00:00:03'

# Port map (must match reroute_topo.py)
S1, S2, S3 = 1, 2, 3
S1_TO_H1, S1_TO_S2, S1_TO_S3 = 1, 2, 3
S2_TO_S1, S2_TO_S3 = 1, 2
S3_TO_H3, S3_TO_S2, S3_TO_S1 = 1, 2, 3

# Ports that make up the PRIMARY path; a down event on any of these => reroute
PRIMARY_PORTS = {(S1, S1_TO_S2), (S2, S2_TO_S1), (S2, S2_TO_S3), (S3, S3_TO_S2)}

PRIO_PRIMARY = 10
PRIO_BACKUP = 100


class SelfHealingReroute(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SelfHealingReroute, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.on_backup = False   # idempotency guard

    # ---- helpers -----------------------------------------------------------
    def _add_flow(self, dp, priority, eth_dst, out_port):
        parser = dp.ofproto_parser
        match = parser.OFPMatch(eth_dst=eth_dst)
        actions = [parser.OFPActionOutput(out_port)]
        inst = [parser.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=dp, priority=priority, match=match,
                                instructions=inst)
        dp.send_msg(mod)

    def _install_primary(self, dp):
        """Proactively install the primary path h1<->h3 through s2."""
        did = dp.id
        if did == S1:
            self._add_flow(dp, PRIO_PRIMARY, H3_MAC, S1_TO_S2)   # to h3 -> via s2
            self._add_flow(dp, PRIO_PRIMARY, H1_MAC, S1_TO_H1)   # to h1 -> local
        elif did == S2:
            self._add_flow(dp, PRIO_PRIMARY, H3_MAC, S2_TO_S3)
            self._add_flow(dp, PRIO_PRIMARY, H1_MAC, S2_TO_S1)
        elif did == S3:
            self._add_flow(dp, PRIO_PRIMARY, H3_MAC, S3_TO_H3)   # to h3 -> local
            self._add_flow(dp, PRIO_PRIMARY, H1_MAC, S3_TO_S2)   # to h1 -> via s2

    def _del_flow(self, dp, priority, eth_dst):
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        dp.send_msg(parser.OFPFlowMod(datapath=dp, command=ofp.OFPFC_DELETE_STRICT,
                                      priority=priority, out_port=ofp.OFPP_ANY,
                                      out_group=ofp.OFPG_ANY,
                                      match=parser.OFPMatch(eth_dst=eth_dst)))

    def restore_primary(self):
        """Failback: when the primary link returns, drop the backup flows so the
        prio-10 primary path (still installed) carries traffic again."""
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

    def trigger_reroute(self):
        """
        Install the BACKUP path (direct s1-s3). Public so the PPO Agent 2 can
        call it later (roadmap sub-step 1.5) instead of relying on auto-detect.
        """
        if self.on_backup:
            return
        s1 = self.datapaths.get(S1)
        s3 = self.datapaths.get(S3)
        if not (s1 and s3):
            self.logger.warning("[REROUTE] s1/s3 not connected yet; cannot reroute")
            return
        # Higher priority => overrides the primary flows immediately
        self._add_flow(s1, PRIO_BACKUP, H3_MAC, S1_TO_S3)   # h1->h3 direct to s3
        self._add_flow(s3, PRIO_BACKUP, H1_MAC, S3_TO_S1)   # h3->h1 direct to s1
        self.on_backup = True
        self.logger.warning("[FAULT] primary path down -> installed BACKUP path s1-s3 (OFPFlowMod)")

    # ---- events ------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features_handler(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        self._install_primary(dp)
        self.logger.info("[INIT] switch s%s connected; primary flows installed", dp.id)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        port_no = msg.desc.port_no
        link_down = bool(msg.desc.state & dp.ofproto.OFPPS_LINK_DOWN)

        if (dp.id, port_no) in PRIMARY_PORTS:
            if link_down:
                self.logger.warning("[DETECT] primary link down at s%s port %s", dp.id, port_no)
                self.trigger_reroute()
            else:
                self.restore_primary()
