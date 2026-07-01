#!/usr/bin/env python3
"""
SHIELD live topology graph (step S1.2).

Holds the switch/link graph the control plane reasons over. It is seeded from the
static spec (topologies/mesh_spec.py) and kept live by port-status events: when a
link goes down the corresponding edge is removed, so `active_graph()` always
reflects what is currently usable. The k-shortest-paths / Resolver work (S2.x)
runs on `active_graph()` with the failed element already excluded.

Everything here is keyed by dpid (what OpenFlow gives us); switch-name helpers
from the spec bridge to the human-readable 's3' form.
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from topologies.mesh_spec import (SWITCHES, HOST_LINKS, SWITCH_LINKS,
                                  build_graph, port_peers, sw_to_dpid, dpid_to_sw)


class TopologyGraph:
    def __init__(self):
        self._spec = build_graph()            # switch-name keyed static adjacency
        self._peers = port_peers()            # (sw,port) -> (peer_sw, peer_port)
        self.down = set()                     # set of frozenset({sw_a, sw_b})

        # dpid -> set of that switch's own port numbers (host + inter-switch),
        # used to validate that observed port-stats match the topology.
        self.switch_ports = {}
        for a, _pa, b, pb, _bw, _d in HOST_LINKS:      # host side is `b`
            self.switch_ports.setdefault(sw_to_dpid(b), set()).add(pb)
        for a, pa, b, pb, _bw, _d in SWITCH_LINKS:
            self.switch_ports.setdefault(sw_to_dpid(a), set()).add(pa)
            self.switch_ports.setdefault(sw_to_dpid(b), set()).add(pb)

    # -- live updates --------------------------------------------------------
    def observe_port_status(self, dpid, port, is_down):
        """Update the graph from an OFPPortStatus event on (dpid, port)."""
        sw = dpid_to_sw(dpid)
        peer = self._peers.get((sw, port))
        if peer is None:
            return  # host-facing or unknown port: not an inter-switch edge
        edge = frozenset({sw, peer[0]})
        if is_down:
            self.down.add(edge)
        else:
            self.down.discard(edge)

    # -- queries -------------------------------------------------------------
    def active_graph(self, exclude_nodes=None, exclude_edges=None):
        """Adjacency (switch-name keyed) with down/excluded links removed."""
        exclude_nodes = set(exclude_nodes or ())
        exclude_edges = {frozenset(e) for e in (exclude_edges or ())}
        g = {}
        for a, nbrs in self._spec.items():
            if a in exclude_nodes:
                continue
            g[a] = {}
            for b, attr in nbrs.items():
                if b in exclude_nodes:
                    continue
                e = frozenset({a, b})
                if e in self.down or e in exclude_edges:
                    continue
                g[a][b] = attr
        return g

    def matches_observed(self, observed_ports):
        """Compare observed {dpid:set(ports)} against the spec. -> (bool, msg)."""
        exp = self.switch_ports
        if set(observed_ports) != set(exp):
            return False, "switch set differs (saw %s, expected %s)" % (
                sorted(observed_ports), sorted(exp))
        for dpid, ports in exp.items():
            if set(observed_ports[dpid]) != set(ports):
                return False, "s%d ports %s != expected %s" % (
                    dpid, sorted(observed_ports[dpid]), sorted(ports))
        return True, "%d switches, all ports match" % len(exp)

    def adjacency_str(self):
        lines = ["[GRAPH] live adjacency (down links: %s):"
                 % (sorted('-'.join(sorted(e)) for e in self.down) or 'none')]
        g = self.active_graph()
        for s in SWITCHES:
            nbrs = ['%s(bw%d,%dms)' % (b, a['bw'], a['delay'])
                    for b, a in sorted(g.get(s, {}).items())]
            lines.append("   %s -> %s" % (s, ', '.join(nbrs) if nbrs else '(isolated)'))
        return '\n'.join(lines)


if __name__ == '__main__':
    # Offline sanity check (no controller needed).
    tg = TopologyGraph()
    print(tg.adjacency_str())
    print("switch_ports:", {k: sorted(v) for k, v in sorted(tg.switch_ports.items())})
