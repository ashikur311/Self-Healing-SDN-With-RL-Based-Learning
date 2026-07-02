#!/usr/bin/env python3
"""
Baseline: widest-path (load-aware max-bottleneck-bandwidth routing).

The strong, standard competitor the S2.5 peer-review demanded (p2 M2). Unlike
shortest_path (latency-blind to load) and fixed_failover (condition-blind), this
picks the surviving path with the greatest OBSERVED bottleneck bandwidth -- i.e. it
DOES react to congestion/bandwidth. It is what a competent traffic-engineered
network would do, so beating (or merely tying) it is the honest bar for SHIELD.

Its blind spot -- and the reason SHIELD can still win -- is that it optimises
bandwidth ONLY: on a high-bandwidth path that has developed packet LOSS (a gray
failure), widest-path still selects it, while SHIELD's loss-aware objective avoids
it. Given the OBSERVED-bandwidth graph, this is otherwise as capable as SHIELD.
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.pathing.k_paths import k_shortest_paths, path_metrics
from baselines.base import DEFAULT_K


class WidestPath:
    name = 'widest_path'

    def select(self, graph, src, dst, link_state=None, seed=None, k=DEFAULT_K):
        cands = k_shortest_paths(graph, src, dst, k=k)
        if not cands:
            return None
        # max bottleneck bw; tie-break lower latency, then path for determinism.
        return max(cands, key=lambda p: (path_metrics(graph, p)['bw'],
                                         -path_metrics(graph, p)['delay'],
                                         [-ord(c) for c in ''.join(p)]))
