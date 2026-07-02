#!/usr/bin/env python3
"""
Baseline: shortest-path recovery (OSPF/Dijkstra-style).

Recomputes the minimum-latency path on the current topology after the fault. It
adapts to *topology* changes but is blind to load: it ignores loss, bandwidth and
congestion, so on a congested-but-up link it will still pick the low-delay path.
This is the standard "just take the shortest route" baseline SHIELD must beat.
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.pathing.k_paths import k_shortest_paths, by_delay
from baselines.base import DEFAULT_K


class ShortestPath:
    name = 'shortest_path'

    def select(self, graph, src, dst, link_state=None, seed=None, k=DEFAULT_K):
        paths = k_shortest_paths(graph, src, dst, k=1, weight=by_delay)
        return paths[0] if paths else None
