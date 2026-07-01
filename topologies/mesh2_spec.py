#!/usr/bin/env python3
"""
Second topology SPEC (step S1.5 / paper item M4) -- structurally different from
mesh_spec so the detector's generalization test (S1.7: train on one topology,
test on an unseen one) is meaningful.

mesh  : 7 switches, 1 two-hop path + 2 three-hop paths.
mesh2 : 6 switches, 2 two-hop paths + 1 three-hop path, different bw/delay mix.

    h1 --- s1 ==(P1: bw8,  3ms)== s2 ============ s6 --- h2
             |=(P2: bw15, 6ms)== s3 ============/
             \=(P3: bw12, 8ms)== s4 == s5 =====/

Same host IPs/MACs as mesh_spec so the traffic generator works unchanged.
The graph helpers are duplicated (small, pure) rather than shared, to keep each
spec self-contained and avoid cross-interpreter import gymnastics.
"""
H1_MAC, H1_IP = '00:00:00:00:00:01', '10.0.0.1'
H2_MAC, H2_IP = '00:00:00:00:00:02', '10.0.0.2'

SWITCHES = ['s1', 's2', 's3', 's4', 's5', 's6']

HOST_LINKS = [
    ('h1', 0, 's1', 1, 100, 1),
    ('h2', 0, 's6', 1, 100, 1),
]
SWITCH_LINKS = [
    # P1: two-hop, low latency, low bw
    ('s1', 2, 's2', 1, 8, 3),
    ('s2', 2, 's6', 2, 8, 3),
    # P2: two-hop, high bw
    ('s1', 3, 's3', 1, 15, 6),
    ('s3', 2, 's6', 3, 15, 6),
    # P3: three-hop, medium
    ('s1', 4, 's4', 1, 12, 8),
    ('s4', 2, 's5', 1, 12, 8),
    ('s5', 2, 's6', 4, 12, 8),
]
INTENDED_PATHS = [
    ['s1', 's2', 's6'],
    ['s1', 's3', 's6'],
    ['s1', 's4', 's5', 's6'],
]
HOST_ATTACH = {'h1': ('s1', 1, H1_MAC, H1_IP),
               'h2': ('s6', 1, H2_MAC, H2_IP)}


def sw_to_dpid(name):
    return int(name[1:])


def dpid_to_sw(dpid):
    return 's%d' % dpid


def build_graph():
    g = {s: {} for s in SWITCHES}
    for a, pa, b, pb, bw, delay in SWITCH_LINKS:
        g[a][b] = {'lport': pa, 'rport': pb, 'bw': bw, 'delay': delay}
        g[b][a] = {'lport': pb, 'rport': pa, 'bw': bw, 'delay': delay}
    return g


def port_peers():
    peers = {}
    for a, pa, b, pb, _bw, _delay in SWITCH_LINKS:
        peers[(a, pa)] = (b, pb)
        peers[(b, pb)] = (a, pa)
    return peers


def node_disjoint_paths(graph, src, dst):
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


if __name__ == '__main__':
    g = build_graph()
    print('mesh2 disjoint s1->s6 paths:')
    for p in node_disjoint_paths(g, 's1', 's6'):
        print('  ', ' - '.join(p))
