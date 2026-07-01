#!/usr/bin/env python3
"""
Pure topology SPEC for the SHIELD mesh (no Mininet import).

Why this file exists: the Mininet builder (mesh.py) can only run under the system
py3.8 (via sudo), but the controller/telemetry/pathing code runs under the venv
py3.9 where Mininet is absent. Both need the same switch/link graph, so the graph
data + graph algorithms live HERE, import-safe from either interpreter. mesh.py
imports these to build the live network; collector/topology_graph/pathing import
them for the control plane.

Switch dpid convention: switch 'sN' has dpid N (s1->1 ... s7->7).
"""

# Hosts ---------------------------------------------------------------------
H1_MAC, H1_IP = '00:00:00:00:00:01', '10.0.0.1'
H2_MAC, H2_IP = '00:00:00:00:00:02', '10.0.0.2'

# Switches, in dpid order (index+1 == dpid).
SWITCHES = ['s1', 's2', 's3', 's4', 's5', 's6', 's7']

# Host links: (host, hport, switch, sport, bw_mbit, delay_ms)
HOST_LINKS = [
    ('h1', 0, 's1', 1, 100, 1),
    ('h2', 0, 's6', 1, 100, 1),
]

# Inter-switch links: (a, pa, b, pb, bw_mbit, delay_ms), grouped by path.
# The per-path bw/delay asymmetry is deliberate: it is what makes "pick the best
# recovery path" a real optimisation and not a coin flip.
SWITCH_LINKS = [
    # Path A: short, low latency, low bandwidth
    ('s1', 2, 's2', 1, 5, 2),
    ('s2', 2, 's6', 2, 5, 2),
    # Path B: balanced
    ('s1', 3, 's3', 1, 10, 5),
    ('s3', 2, 's4', 1, 10, 5),
    ('s4', 2, 's6', 3, 10, 5),
    # Path C: long, high latency, high bandwidth
    ('s1', 4, 's5', 1, 20, 10),
    ('s5', 2, 's7', 1, 20, 10),
    ('s7', 2, 's6', 4, 20, 10),
]

# The three intended host-to-host paths (switch level), for verification/reuse.
INTENDED_PATHS = [
    ['s1', 's2', 's6'],
    ['s1', 's3', 's4', 's6'],
    ['s1', 's5', 's7', 's6'],
]

# Which switch each host hangs off, and on which switch port.
HOST_ATTACH = {'h1': ('s1', 1, H1_MAC, H1_IP),
               'h2': ('s6', 1, H2_MAC, H2_IP)}


def sw_to_dpid(name):
    """'s3' -> 3."""
    return int(name[1:])


def dpid_to_sw(dpid):
    """3 -> 's3'."""
    return 's%d' % dpid


def build_graph():
    """Switch-level adjacency with link attributes, keyed by switch name.

    Shape: { sX: { sY: {'lport': int, 'rport': int, 'bw': int, 'delay': int} } }
    """
    g = {s: {} for s in SWITCHES}
    for a, pa, b, pb, bw, delay in SWITCH_LINKS:
        g[a][b] = {'lport': pa, 'rport': pb, 'bw': bw, 'delay': delay}
        g[b][a] = {'lport': pb, 'rport': pa, 'bw': bw, 'delay': delay}
    return g


def port_peers():
    """Map (switch, port) -> (peer_switch, peer_port) for every switch link.

    Lets the control plane translate a local port number (as seen in port-stats
    or a port-down event) into the neighbour it connects to.
    """
    peers = {}
    for a, pa, b, pb, _bw, _delay in SWITCH_LINKS:
        peers[(a, pa)] = (b, pb)
        peers[(b, pb)] = (a, pa)
    return peers


def node_disjoint_paths(graph, src, dst):
    """Greedy vertex-disjoint path finder (Menger lower bound).

    Repeatedly take a shortest path (BFS), remove its *intermediate* vertices so
    the next path cannot reuse them. The count returned is a valid lower bound on
    the max number of internally node-disjoint paths; for this mesh it finds all
    three. Reused by the pathing/Resolver work (S2.x).
    """
    remaining = {u: set(nbrs) for u, nbrs in graph.items()}
    paths = []
    while True:
        prev, seen, queue = {src: None}, {src}, [src]
        while queue:
            u = queue.pop(0)
            if u == dst:
                break
            for v in remaining.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    prev[v] = u
                    queue.append(v)
        if dst not in prev:
            break
        path, node = [], dst
        while node is not None:
            path.append(node)
            node = prev[node]
        path.reverse()
        paths.append(path)
        for mid in path[1:-1]:
            for w in remaining.pop(mid, ()):
                remaining[w].discard(mid)
    return paths
