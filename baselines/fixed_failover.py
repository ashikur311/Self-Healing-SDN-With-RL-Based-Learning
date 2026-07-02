#!/usr/bin/env python3
"""
Baseline: fixed (pre-provisioned) failover.

Models the classic OpenFlow fast-failover group (see controller/failover_agent.py):
a static, ordered list of backup paths is decided OFFLINE from the nominal topology
and installed ahead of time. On a fault the switch simply drops to the next backup
in that fixed order that is still up -- it never recomputes and never looks at live
link quality. This is the "dumb but instant" lower/again-standard baseline.

The preference order is fixed at construction from the nominal graph (shortest by
hops, then latency). At select() time we return the first preferred path whose hops
all survive in the current (post-fault) graph.
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.pathing.k_paths import k_shortest_paths, path_metrics
from baselines.base import DEFAULT_K, path_is_viable


class FixedFailover:
    name = 'fixed_failover'

    def __init__(self, nominal_graph, src, dst, k=DEFAULT_K):
        # Freeze the backup order offline, from the healthy topology.
        cands = k_shortest_paths(nominal_graph, src, dst, k=k)
        self._order = sorted(
            cands, key=lambda p: (path_metrics(nominal_graph, p)['hops'],
                                  path_metrics(nominal_graph, p)['delay'],
                                  tuple(p)))

    def select(self, graph, src, dst, link_state=None, seed=None, k=DEFAULT_K):
        for p in self._order:                 # fixed, pre-decided order
            if path_is_viable(graph, p):       # first still-up backup wins
                return p
        return None
