#!/usr/bin/env python3
"""
SHIELD fault localizer (step S1.6, hybrid design).

Localization is done from OpenFlow port-status (link-down) events, not from
throughput thresholds. This is exact, immediate, and -- crucially -- immune to
congestion: a congested link stays UP and never raises a port-down, so it can
never be mislocalized as a fault. Telemetry (the collector's recent activity) is
used only to decide whether the failed element was actually carrying traffic
(service-impacting) so the resolver reroutes what matters.

Rationale (see docs/p1_detection_results.txt): pure-telemetry localization is
infeasible on a shared data plane -- when a link dies, its innocent downstream
neighbours lose traffic too and look identical. Port-status disambiguates the
exact failed element; the ML/telemetry path is reserved for gray failures that
never trigger a port-down (future work).
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class FaultLocalizer:
    def __init__(self, topology_graph, collector, active_kbps=100.0):
        self.topo = topology_graph          # TopologyGraph (tracks down links)
        self.collector = collector          # TelemetryCollector (recent activity)
        self.active_kbps = active_kbps

    def on_port_down(self, dpid, port):
        """Return a localized fault event for a port-down, or None if the port
        is host-facing / unknown (not an inter-switch link)."""
        from topologies.mesh_spec import dpid_to_sw  # spec-agnostic name helper
        sw = dpid_to_sw(dpid)
        peer = self.topo._peers.get((sw, port))
        if peer is None:
            return None                      # host/edge port -- not a routed link
        from topologies.mesh_spec import sw_to_dpid
        failed = [(dpid, port), (sw_to_dpid(peer[0]), peer[1])]
        # keep the graph's down-set in sync for the resolver
        self.topo.observe_port_status(dpid, port, True)

        # Was the failed element carrying traffic? (service-impacting)
        recent = max(self.collector.telemetry.get(fp, {}).get('recent_max_kbps', 0.0)
                     for fp in failed)
        return {'kind': 'hard_fault',
                'failed_link': (sw, peer[0]),
                'failed_ports': failed,
                'service_impacting': recent > self.active_kbps,
                'recent_max_kbps': round(recent, 1)}

    def on_port_up(self, dpid, port):
        self.topo.observe_port_status(dpid, port, False)
