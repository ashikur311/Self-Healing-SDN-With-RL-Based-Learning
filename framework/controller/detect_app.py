#!/usr/bin/env python3
"""
SHIELD detection controller (steps S1.6/S1.7, hybrid design).

A Ryu app that combines:
  * TelemetryCollector  -- per-port [tput, loss, delay, drop_ratio] (S1.2),
  * TopologyGraph       -- live graph, edges drop on link-down (S1.2),
  * FaultLocalizer      -- exact failed (dpid,port) from port-status (S1.6).

On a port-down it emits a localized, timestamped detection to
results/logs/detections.json; the S1.7 experiment matches these against the
ground-truth fault log. Congestion never raises a port-down, so it is never
emitted as a fault -- the detector's precision on congestion is 1.0 by design.

Run (venv):  ryu-manager framework/controller/detect_app.py
"""
import os
import sys
import json
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
from framework.telemetry.topology_graph import TopologyGraph
from framework.agents.localizer import FaultLocalizer

POLL_SECONDS = 2
DETECTIONS = os.path.join(_ROOT, 'results', 'logs', 'detections.json')


class DetectApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(DetectApp, self).__init__(*args, **kwargs)
        self.collector = TelemetryCollector()
        self.topo = TopologyGraph()
        self.localizer = FaultLocalizer(self.topo, self.collector)
        self.datapaths = {}
        self.detections = []
        self.t0 = time.time()
        self._save()                     # start each experiment with a clean file
        hub.spawn(self._monitor)
        self.logger.info("[S1.6] DetectApp up (hybrid: port-status localization)")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        parser, ofp = dp.ofproto_parser, dp.ofproto
        actions = [parser.OFPActionOutput(ofp.OFPP_NORMAL)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=0,
                                      match=parser.OFPMatch(), instructions=inst))

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status(self, ev):
        msg = ev.msg
        dpid = msg.datapath.id
        port = msg.desc.port_no
        down = bool(msg.desc.state & msg.datapath.ofproto.OFPPS_LINK_DOWN)
        t = round(time.time() - self.t0, 2)
        if down:
            evt = self.localizer.on_port_down(dpid, port)
            if evt is None:
                return
            evt['t'] = t
            evt['wall'] = time.time()
            self.detections.append(evt)
            self.logger.warning("[DETECT t=%6.2f] FAULT link %s-%s ports=%s "
                                "service_impacting=%s (recent_max=%.0f kbps)",
                                t, evt['failed_link'][0], evt['failed_link'][1],
                                evt['failed_ports'], evt['service_impacting'],
                                evt['recent_max_kbps'])
            self._save()
        else:
            self.localizer.on_port_up(dpid, port)

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

    def _save(self):
        try:
            with open(DETECTIONS, 'w') as f:
                json.dump({'detections': self.detections}, f, indent=2)
        except Exception as e:
            self.logger.debug("save failed: %s", e)
