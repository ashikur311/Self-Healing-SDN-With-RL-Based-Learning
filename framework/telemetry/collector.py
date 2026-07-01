#!/usr/bin/env python3
"""
SHIELD telemetry collector (step S1.2).

Two things live here:

1. TelemetryCollector -- a Ryu-independent, reusable class that turns raw
   OFPPortStats into a normalized per-port state [throughput, loss, latency].
   The math is lifted from controller/self_healing_agent.py (byte-delta
   throughput, stats-request round-trip as the latency proxy) and extended with
   a real loss estimate from the switch drop/error counters instead of the old
   "zero-throughput => 100% loss" heuristic. The future closed-loop controller
   (shield_app, S2.4) and the detector (S1.6) both reuse this class.

2. TelemetryMonitor -- a thin standalone Ryu app used to VALIDATE S1.2: it
   installs a NORMAL-forwarding flow so traffic flows (run the mesh with STP),
   polls stats, logs the per-port telemetry table, and dumps a JSON snapshot to
   results/logs/telemetry_snapshot.json for the test harness to assert on.

Run the monitor (venv Ryu):
    /home/mininet/venvs/ryu-4.34-py39/bin/ryu-manager \
        --observe-links framework/telemetry/collector.py
"""
import os
import sys
import json
import time

OFPP_LOCAL = 0xfffffffe

# Normalization constants (match the paper's state scaling so a model trained on
# one is comparable to the other).
TPUT_NORM_KBPS = 25000.0
DELAY_NORM_MS = 100.0


class TelemetryCollector:
    """Stateful converter: raw OFPPortStats bodies -> normalized per-port state."""

    def __init__(self, tput_norm_kbps=TPUT_NORM_KBPS,
                 delay_norm_ms=DELAY_NORM_MS, skip_local=True,
                 window=6, min_active_kbps=100.0):
        self.tput_norm_kbps = tput_norm_kbps
        self.delay_norm_ms = delay_norm_ms
        self.skip_local = skip_local
        # (dpid,port) -> (bytes, packets, drops_errs, timestamp)
        self._prev = {}
        # (dpid,port) -> {'throughput_kbps','loss','delay_ms','recent_max_kbps','drop_ratio'}
        self.telemetry = {}
        # dpid -> timestamp the last stats request was sent (latency proxy)
        self._req_times = {}
        # trailing throughput history per port -> recent_max + drop_ratio, the
        # feature that separates "was active, now silent" (fault) from "always
        # idle" (normal). Needed both for the dataset and the live detector.
        self.window = window
        self.min_active_kbps = min_active_kbps
        self._hist = {}

    # -- called by the polling loop / event handlers -------------------------
    def mark_request(self, dpid, now=None):
        self._req_times[dpid] = now if now is not None else time.time()

    def update_from_reply(self, dpid, body, now=None):
        """Fold one OFPPortStatsReply body into telemetry; return updated keys."""
        now = now if now is not None else time.time()
        delay_ms = round((now - self._req_times.get(dpid, now)) * 1000.0, 2)
        updated = []
        for stat in body:
            port = stat.port_no
            if self.skip_local and port == OFPP_LOCAL:
                continue
            key = (dpid, port)
            cur_bytes = stat.rx_bytes + stat.tx_bytes
            cur_pkts = stat.rx_packets + stat.tx_packets
            cur_bad = (getattr(stat, 'rx_dropped', 0) + getattr(stat, 'tx_dropped', 0)
                       + getattr(stat, 'rx_errors', 0) + getattr(stat, 'tx_errors', 0))

            tput_kbps, loss = 0.0, 0.0
            if key in self._prev:
                pb, pp, pbad, pt = self._prev[key]
                dt = now - pt
                if dt > 0:
                    tput_kbps = ((cur_bytes - pb) * 8.0) / (dt * 1000.0)
                d_pkts = max(cur_pkts - pp, 0)
                d_bad = max(cur_bad - pbad, 0)
                denom = d_pkts + d_bad
                if denom > 0:
                    loss = d_bad / float(denom)
            self._prev[key] = (cur_bytes, cur_pkts, cur_bad, now)

            hist = self._hist.setdefault(key, [])
            hist.append(tput_kbps)
            if len(hist) > self.window:
                del hist[0]
            recent_max = max(hist)
            drop_ratio = 0.0
            if recent_max > self.min_active_kbps:
                drop_ratio = max(0.0, (recent_max - tput_kbps) / recent_max)

            self.telemetry[key] = {'throughput_kbps': round(tput_kbps, 3),
                                   'loss': round(loss, 4),
                                   'delay_ms': delay_ms,
                                   'recent_max_kbps': round(recent_max, 3),
                                   'drop_ratio': round(drop_ratio, 4)}
            updated.append(key)
        return updated

    # -- consumers -----------------------------------------------------------
    def normalized(self, key):
        """Return [throughput_n, loss, delay_n] in [0,1], matching paper scaling."""
        t = self.telemetry.get(key)
        if t is None:
            return [0.0, 0.0, 0.0]
        return [min(t['throughput_kbps'] / self.tput_norm_kbps, 1.0),
                min(t['loss'], 1.0),
                min(t['delay_ms'] / self.delay_norm_ms, 1.0)]

    def feature_vector(self, key):
        """Detector features [tput_n, loss_n, delay_n, drop_ratio] in [0,1].

        Matches framework/agents/detector.py FEATURES; used by the live closed
        loop (S2.4) so the online detector sees the same inputs as training.
        """
        t = self.telemetry.get(key)
        if t is None:
            return [0.0, 0.0, 0.0, 0.0]
        return [min(t['throughput_kbps'] / self.tput_norm_kbps, 1.0),
                min(t['loss'], 1.0),
                min(t['delay_ms'] / self.delay_norm_ms, 1.0),
                t['drop_ratio']]

    def snapshot(self):
        """Plain-dict copy keyed by 'dpid:port' (JSON-friendly)."""
        return {'%d:%d' % k: dict(v) for k, v in self.telemetry.items()}


# ---------------------------------------------------------------------------
# Standalone validation app (only imported when run under ryu-manager).
# ---------------------------------------------------------------------------
try:
    from ryu.base import app_manager
    from ryu.controller import ofp_event
    from ryu.controller.handler import (CONFIG_DISPATCHER, MAIN_DISPATCHER,
                                        set_ev_cls)
    from ryu.ofproto import ofproto_v1_3
    from ryu.lib import hub
    _HAVE_RYU = True
except Exception:  # imported outside ryu-manager (e.g. unit test)
    _HAVE_RYU = False

if _HAVE_RYU:
    # Make `topologies.*` importable when launched from the project root.
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from framework.telemetry.topology_graph import TopologyGraph

    POLL_SECONDS = 3
    SNAPSHOT_PATH = os.path.join(_ROOT, 'results', 'logs', 'telemetry_snapshot.json')

    class TelemetryMonitor(app_manager.RyuApp):
        OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

        def __init__(self, *args, **kwargs):
            super(TelemetryMonitor, self).__init__(*args, **kwargs)
            self.collector = TelemetryCollector()
            self.topo = TopologyGraph()
            self.datapaths = {}
            self.observed_ports = {}   # dpid -> set(port_no)
            self._printed_graph = False
            self.monitor_thread = hub.spawn(self._monitor)
            self.logger.info("[S1.2] TelemetryMonitor up; polling every %ss", POLL_SECONDS)

        @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
        def _features(self, ev):
            dp = ev.msg.datapath
            self.datapaths[dp.id] = dp
            parser, ofp = dp.ofproto_parser, dp.ofproto
            # Priority-0 NORMAL flow: OVS does normal L2 forwarding (loop-safe
            # because the mesh is brought up with STP for this test). Production
            # forwarding is installed by shield_app (S2.4), not here.
            actions = [parser.OFPActionOutput(ofp.OFPP_NORMAL)]
            inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
            dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=0,
                                          match=parser.OFPMatch(), instructions=inst))
            self.logger.info("[INIT] s%s connected; NORMAL forwarding installed", dp.id)

        @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
        def _port_status(self, ev):
            msg = ev.msg
            dpid = msg.datapath.id
            down = bool(msg.desc.state & msg.datapath.ofproto.OFPPS_LINK_DOWN)
            self.topo.observe_port_status(dpid, msg.desc.port_no, down)
            self.logger.warning("[PORT] s%s port %s %s", dpid, msg.desc.port_no,
                                "DOWN" if down else "UP")

        def _monitor(self):
            while True:
                for dp in list(self.datapaths.values()):
                    self.collector.mark_request(dp.id)
                    parser = dp.ofproto_parser
                    dp.send_msg(parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))
                hub.sleep(POLL_SECONDS)

        @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
        def _stats_reply(self, ev):
            dpid = ev.msg.datapath.id
            keys = self.collector.update_from_reply(dpid, ev.msg.body)
            self.observed_ports.setdefault(dpid, set()).update(k[1] for k in keys)

            for (d, p) in sorted(keys):
                t = self.collector.telemetry[(d, p)]
                self.logger.info("  s%s:%-3s  tput=%9.2f kbps  loss=%.3f  delay=%6.2f ms",
                                 d, p, t['throughput_kbps'], t['loss'], t['delay_ms'])

            # Once every switch has reported, print the graph and dump a snapshot.
            if (not self._printed_graph
                    and len(self.observed_ports) == len(self.topo.switch_ports)):
                self.logger.info("\n%s", self.topo.adjacency_str())
                match, msg = self.topo.matches_observed(self.observed_ports)
                self.logger.info("[GRAPH] matches topology spec: %s (%s)", match, msg)
                self._printed_graph = True
            try:
                with open(SNAPSHOT_PATH, 'w') as f:
                    json.dump(self.collector.snapshot(), f, indent=2)
            except Exception as e:  # non-fatal for the live loop
                self.logger.debug("snapshot write failed: %s", e)
