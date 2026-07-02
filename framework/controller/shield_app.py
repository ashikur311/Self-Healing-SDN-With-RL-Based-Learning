#!/usr/bin/env python3
"""
SHIELD closed-loop controller (step S2.4) -- the production Ryu app.

Wires the full loop on the live mesh:
    port-stats telemetry (TelemetryCollector)
      -> fault localization (FaultLocalizer, from port-status)
      -> best-path resolution (ShieldCore -> Resolver over k-shortest-paths)
      -> actuation (OFPFlowMod install of the chosen path)
      -> failback on link-up.

The decision logic lives in ShieldCore (Ryu-free) so it is unit-tested offline and
shared with the single-process integration experiment; this file is only the Ryu
event shell + OFPFlowMod actuator. Switches are fail-secure: the app installs the
PRIMARY path proactively at low priority when each switch connects, then overrides
with the resolved backup path at high priority on a fault.

Run (venv):  ryu-manager framework/controller/shield_app.py
Pair with:   sudo python3 topologies/mesh.py        (fail-secure, remote controller)
"""
import os
import sys
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.telemetry.collector import TelemetryCollector
from framework.agents.localizer import FaultLocalizer
from framework.controller.shield_core import ShieldCore, PRIO_PRIMARY, PRIO_BACKUP
from topologies.mesh_spec import sw_to_dpid

POLL_SECONDS = 2


class ShieldApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ShieldApp, self).__init__(*args, **kwargs)
        self.collector = TelemetryCollector()
        self.core = ShieldCore()                     # owns TopologyGraph + Resolver
        self.localizer = FaultLocalizer(self.core.topo, self.collector)
        self.datapaths = {}
        self.t0 = time.time()
        # Pre-index the proactive PRIMARY plan by dpid so each switch installs its
        # own entries the moment it connects.
        self._primary_by_dpid = {}
        for e in self.core.initial_plan():
            self._primary_by_dpid.setdefault(e['dpid'], []).append(e)
        hub.spawn(self._monitor)
        self.logger.info("[S2.4] ShieldApp up; primary=%s",
                         '->'.join(self.core.primary))

    # -- actuation helpers ---------------------------------------------------
    def _add_flow(self, dp, priority, eth_dst, out_port):
        p = dp.ofproto_parser
        match = p.OFPMatch(eth_dst=eth_dst)
        inst = [p.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS,
                                        [p.OFPActionOutput(out_port)])]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=priority,
                                 match=match, instructions=inst))

    def _del_flow(self, dp, priority, eth_dst):
        p, ofp = dp.ofproto_parser, dp.ofproto
        dp.send_msg(p.OFPFlowMod(datapath=dp, command=ofp.OFPFC_DELETE_STRICT,
                                 priority=priority, out_port=ofp.OFPP_ANY,
                                 out_group=ofp.OFPG_ANY,
                                 match=p.OFPMatch(eth_dst=eth_dst)))

    def _apply(self, delta):
        """Install/delete the flow entries produced by ShieldCore.plan_recovery."""
        if not delta:
            return
        prio = delta['priority']
        for e in delta.get('install', []):
            dp = self.datapaths.get(e['dpid'])
            if dp:
                self._add_flow(dp, prio, e['eth_dst'], e['out_port'])
        for e in delta.get('delete', []):
            dp = self.datapaths.get(e['dpid'])
            if dp:
                self._del_flow(dp, prio, e['eth_dst'])
        self.logger.warning("[%s t=%6.2f] -> %s", delta['action'].upper(),
                            time.time() - self.t0, '->'.join(delta['path']))

    # -- events --------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        # Proactively install this switch's PRIMARY entries (if it is on the path).
        for e in self._primary_by_dpid.get(dp.id, []):
            self._add_flow(dp, PRIO_PRIMARY, e['eth_dst'], e['out_port'])
        self.logger.info("[INIT] s%s connected; primary flows installed", dp.id)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status(self, ev):
        msg = ev.msg
        dpid, port = msg.datapath.id, msg.desc.port_no
        down = bool(msg.desc.state & msg.datapath.ofproto.OFPPS_LINK_DOWN)
        if down:
            evt = self.localizer.on_port_down(dpid, port)   # updates core.topo.down
            if evt is None:
                return
            self.logger.warning("[DETECT t=%6.2f] fault link %s-%s",
                                time.time() - self.t0, *evt['failed_link'])
        else:
            self.localizer.on_port_up(dpid, port)
        self._apply(self.core.plan_recovery())

    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                self.collector.mark_request(dp.id)
                p = dp.ofproto_parser
                dp.send_msg(p.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))
            hub.sleep(POLL_SECONDS)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _stats_reply(self, ev):
        self.collector.update_from_reply(ev.msg.datapath.id, ev.msg.body)
