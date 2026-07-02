#!/usr/bin/env python3
"""
Unit test for step S2.1 — Yen's k-shortest-paths (framework/pathing/k_paths.py).

Runs offline (no Mininet/controller). Three checks:
  1. Hand-drawn graph with distinct, known path costs -> exact k-order.
  2. Real mesh spec -> the 3 intended node-disjoint paths, ordered by delay.
  3. Exclusion -> failed node/edge is absent from every returned path.

PASS: all asserts hold; run `python3 tests/test_k_paths.py` -> "S2.1 PASS".
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.pathing.k_paths import (k_shortest_paths, dijkstra, path_cost,
                                       path_metrics, candidate_paths)
from framework.telemetry.topology_graph import TopologyGraph
from topologies.mesh_spec import build_graph


def _mk(edges):
    """Build a symmetric adjacency {a:{b:{'delay','bw'}}} from (a,b,delay,bw)."""
    g = {}
    for a, b, delay, bw in edges:
        g.setdefault(a, {})[b] = {'delay': delay, 'bw': bw, 'lport': 0, 'rport': 0}
        g.setdefault(b, {})[a] = {'delay': delay, 'bw': bw, 'lport': 0, 'rport': 0}
    return g


def test_handdrawn_order():
    # A->D paths by delay: A-B-C-D=3, A-C-D=5, A-B-D=6, A-C-B-D=10 (all distinct).
    g = _mk([('A', 'B', 1, 10), ('A', 'C', 4, 10), ('B', 'C', 1, 10),
             ('B', 'D', 5, 10), ('C', 'D', 1, 10)])
    paths = k_shortest_paths(g, 'A', 'D', k=3)
    assert paths == [['A', 'B', 'C', 'D'], ['A', 'C', 'D'], ['A', 'B', 'D']], paths
    costs = [path_cost(g, p) for p in paths]
    assert costs == [3, 5, 6], costs
    assert costs == sorted(costs), "not ascending: %s" % costs
    # k larger than #paths returns all 4 (the 4th is A-C-B-D=10).
    allp = k_shortest_paths(g, 'A', 'D', k=99)
    assert len(allp) == 4, allp
    assert allp[-1] == ['A', 'C', 'B', 'D'], allp[-1]
    print("  [1] hand-drawn k-order + cost ascending .......... OK")


def test_mesh_three_paths():
    g = build_graph()
    paths = k_shortest_paths(g, 's1', 's6', k=3)
    assert len(paths) == 3, paths
    # deterministic order by summed delay: A(4) < B(15) < C(30)
    assert paths[0] == ['s1', 's2', 's6'], paths[0]
    assert paths[1] == ['s1', 's3', 's4', 's6'], paths[1]
    assert paths[2] == ['s1', 's5', 's7', 's6'], paths[2]
    assert [path_metrics(g, p)['delay'] for p in paths] == [4, 15, 30]
    # bottleneck bw per path: A=5, B=10, C=20
    assert [path_metrics(g, p)['bw'] for p in paths] == [5, 10, 20]
    print("  [2] mesh: 3 disjoint paths, delay-ordered ....... OK")


def test_exclusion():
    g = build_graph()
    # Exclude node s2 -> Path A unusable; only B and C remain.
    tg = TopologyGraph()
    paths = candidate_paths(tg, 's1', 's6', k=3, failed_node='s2')
    assert len(paths) == 2, paths
    assert all('s2' not in p for p in paths), paths
    assert paths == [['s1', 's3', 's4', 's6'], ['s1', 's5', 's7', 's6']], paths

    # Exclude edge s1-s3 -> Path B's first hop gone; A and C remain.
    paths2 = candidate_paths(tg, 's1', 's6', k=3, failed_edge=('s1', 's3'))
    assert len(paths2) == 2, paths2
    for p in paths2:
        for a, b in zip(p, p[1:]):
            assert {a, b} != {'s1', 's3'}, p
    print("  [3] exclusion: failed node/edge absent .......... OK")


def test_unreachable():
    g = _mk([('A', 'B', 1, 10)])
    assert dijkstra(g, 'A', 'Z') is None
    assert k_shortest_paths(g, 'A', 'Z', k=3) == []
    assert dijkstra(g, 'A', 'A') == ['A']
    print("  [4] unreachable/degenerate handled .............. OK")


if __name__ == '__main__':
    test_handdrawn_order()
    test_mesh_three_paths()
    test_exclusion()
    test_unreachable()
    print("S2.1 PASS")
