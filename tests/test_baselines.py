#!/usr/bin/env python3
"""
Unit test for step S2.3 — recovery baselines (baselines/*.py).

Offline (no Mininet/controller). Checks:
  1. Each baseline returns a VALID, fault-avoiding path on the healthy mesh and
     after excluding a failed element (uniform contract).
  2. Distinguishing behaviour that S2.5 relies on:
       - shortest_path ignores loss -> keeps low-delay Path A even when A is lossy;
       - oracle (full knowledge) avoids the lossy Path A under the same link_state;
       - fixed_failover uses its pre-decided order (independent of link_state);
       - random_path is seed-reproducible and always viable.

PASS: all asserts hold; `python3 tests/test_baselines.py` -> "S2.3 PASS".
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from topologies.mesh_spec import build_graph
from framework.telemetry.topology_graph import TopologyGraph
from baselines.base import path_is_viable
from baselines.shortest_path import ShortestPath
from baselines.fixed_failover import FixedFailover
from baselines.random_path import RandomPath
from baselines.oracle_path import OraclePath


def _active(exclude_edge=None, exclude_node=None):
    tg = TopologyGraph()
    return tg.active_graph(
        exclude_edges=[exclude_edge] if exclude_edge else None,
        exclude_nodes=[exclude_node] if exclude_node else None)


def test_all_return_valid_paths():
    nominal = build_graph()
    selectors = [ShortestPath(), FixedFailover(nominal, 's1', 's6'),
                 RandomPath(), OraclePath()]
    # healthy graph
    for s in selectors:
        p = s.select(nominal, 's1', 's6', seed=0)
        assert p and p[0] == 's1' and p[-1] == 's6', (s.name, p)
        assert path_is_viable(nominal, p), (s.name, p)
    # after a node failure (s2 -> Path A gone): still valid, and avoids s2
    g = _active(exclude_node='s2')
    for s in selectors:
        p = s.select(g, 's1', 's6', seed=0)
        assert p and path_is_viable(g, p), (s.name, p)
        assert 's2' not in p, (s.name, p)
    print("  [1] all baselines return valid fault-avoiding paths  OK")


def test_shortest_vs_oracle_under_loss():
    g = build_graph()
    # 60% loss on Path A's first link. Full-knowledge oracle should avoid A;
    # loss-blind shortest-path should still take A (lowest delay).
    link_state = {frozenset({'s1', 's2'}): 0.6}
    sp = ShortestPath().select(g, 's1', 's6', link_state=link_state)
    orc = OraclePath().select(g, 's1', 's6', link_state=link_state)
    assert sp == ['s1', 's2', 's6'], sp            # ignores loss
    assert 's2' not in orc, orc                     # full knowledge avoids lossy A
    print("  [2] shortest ignores loss, oracle avoids it ......... OK")


def test_fixed_failover_order():
    nominal = build_graph()
    ff = FixedFailover(nominal, 's1', 's6')
    # Healthy: fixed order prefers fewest hops -> Path A (2 hops).
    assert ff.select(nominal, 's1', 's6') == ['s1', 's2', 's6']
    # link_state must not change a FIXED failover's choice.
    lossy = {frozenset({'s1', 's2'}): 0.9}
    assert ff.select(nominal, 's1', 's6', link_state=lossy) == ['s1', 's2', 's6']
    # If A's link is down, it drops to the next pre-decided backup.
    g = _active(exclude_edge=('s1', 's2'))
    nxt = ff.select(g, 's1', 's6')
    assert nxt and 's2' not in nxt and path_is_viable(g, nxt), nxt
    print("  [3] fixed_failover uses pre-decided order ........... OK")


def test_random_reproducible():
    g = build_graph()
    r = RandomPath()
    a = r.select(g, 's1', 's6', seed=42)
    b = r.select(g, 's1', 's6', seed=42)
    assert a == b and path_is_viable(g, a), (a, b)
    # different seeds should be able to differ across the 3 candidates
    choices = {tuple(r.select(g, 's1', 's6', seed=i)) for i in range(20)}
    assert len(choices) >= 2, choices
    print("  [4] random_path seed-reproducible + varied .......... OK")


if __name__ == '__main__':
    test_all_return_valid_paths()
    test_shortest_vs_oracle_under_loss()
    test_fixed_failover_order()
    test_random_reproducible()
    print("S2.3 PASS")
