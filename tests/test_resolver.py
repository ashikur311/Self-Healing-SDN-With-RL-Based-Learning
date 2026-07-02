#!/usr/bin/env python3
"""
Unit test for step S2.2 — Resolver scoring/selection (framework/agents/resolver.py).

Offline (no Mininet/controller). Checks:
  1. Dominant path: a path best in every dimension is chosen under any weights.
  2. Weight-sensitivity: from the SAME candidates, a latency-priority operator and
     a capacity-priority operator pick DIFFERENT paths (proves the score is real,
     not a relabelled shortest-path).
  3. Loss avoidance: injecting per-link loss via link_state steers away from the
     otherwise-best path.
  4. flow_plan: chosen switch-path -> correct per-switch (dpid, out_port) entries.

PASS: all asserts hold; `python3 tests/test_resolver.py` -> "S2.2 PASS".
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.agents.resolver import Resolver, path_qos, flow_plan
from topologies.mesh_spec import build_graph


def _mk(edges):
    g = {}
    for a, b, delay, bw in edges:
        g.setdefault(a, {})[b] = {'delay': delay, 'bw': bw, 'lport': 0, 'rport': 0}
        g.setdefault(b, {})[a] = {'delay': delay, 'bw': bw, 'lport': 0, 'rport': 0}
    return g


def test_dominant_path():
    # Direct edge X=[s->d]: lat 2, bw 20, hops 1 dominates detour Y=[s-m-d].
    g = _mk([('s', 'd', 2, 20), ('s', 'm', 10, 5), ('m', 'd', 10, 5)])
    for w in ({'latency': 1, 'capacity': 0, 'loss': 0, 'hops': 0},
              {'latency': 0, 'capacity': 1, 'loss': 0, 'hops': 0},
              None):  # default
        best = Resolver(w).select(g, 's', 'd', k=3)
        assert best['path'] == ['s', 'd'], (w, best['path'])
    print("  [1] dominant path chosen under any weights ...... OK")


def test_weight_sensitivity():
    # Mesh: A(lat4,bw5) B(lat15,bw10) C(lat30,bw20). Latency-priority -> A;
    # capacity-priority -> C. Same candidate set, different winners.
    g = build_graph()
    lat_best = Resolver({'latency': 1, 'capacity': 0, 'loss': 0, 'hops': 0}
                        ).select(g, 's1', 's6', k=3)
    cap_best = Resolver({'latency': 0, 'capacity': 1, 'loss': 0, 'hops': 0}
                        ).select(g, 's1', 's6', k=3)
    assert lat_best['path'] == ['s1', 's2', 's6'], lat_best['path']
    assert cap_best['path'] == ['s1', 's5', 's7', 's6'], cap_best['path']
    assert lat_best['path'] != cap_best['path']
    print("  [2] weight-sensitivity: lat->A, cap->C .......... OK")


def test_loss_avoidance():
    # Path A is lowest-latency, but give its s1-s2 link 50% loss under a
    # loss-priority policy -> resolver must abandon A.
    g = build_graph()
    link_state = {frozenset({'s1', 's2'}): 0.5}
    r = Resolver({'latency': 0.2, 'capacity': 0, 'loss': 1.0, 'hops': 0})
    best = r.select(g, 's1', 's6', k=3, link_state=link_state)
    assert 's2' not in best['path'], best['path']
    # sanity: without the loss, latency-priority would have kept A
    clean = Resolver({'latency': 1, 'capacity': 0, 'loss': 0, 'hops': 0}
                     ).select(g, 's1', 's6', k=3)
    assert clean['path'] == ['s1', 's2', 's6']
    # and path_qos reports the injected loss on A
    qa = path_qos(g, ['s1', 's2', 's6'], link_state)
    assert qa['loss'] == 0.5, qa
    print("  [3] loss avoidance via link_state ............... OK")


def test_flow_plan():
    g = build_graph()
    plan = flow_plan(g, ['s1', 's2', 's6'])   # Path A
    fwd = {e['dpid']: e['out_port'] for e in plan if e['dir'] == 'fwd'}
    rev = {e['dpid']: e['out_port'] for e in plan if e['dir'] == 'rev'}
    # forward to h2: s1->s2 port2, s2->s6 port2, s6->host port1
    assert fwd == {1: 2, 2: 2, 6: 1}, fwd
    # reverse to h1: s6->s2 port2, s2->s1 port1, s1->host port1
    assert rev == {6: 2, 2: 1, 1: 1}, rev
    # every entry carries a MAC and a direction
    assert all(e['eth_dst'] and e['dir'] in ('fwd', 'rev') for e in plan)
    print("  [4] flow_plan out-ports match spec .............. OK")


if __name__ == '__main__':
    test_dominant_path()
    test_weight_sensitivity()
    test_loss_avoidance()
    test_flow_plan()
    print("S2.2 PASS")
