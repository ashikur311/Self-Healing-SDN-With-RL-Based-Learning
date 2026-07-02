#!/usr/bin/env python3
"""
Baseline: random viable path (control / naive lower bound).

Picks uniformly at random among the k candidate paths that avoid the failed
element. It has no notion of "better" -- it is the control that shows how much the
Resolver's scoring actually buys over choosing *any* working path. Seeded for
reproducibility across the S2.5 seeds.
"""
import os
import random
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.pathing.k_paths import k_shortest_paths
from baselines.base import DEFAULT_K


class RandomPath:
    name = 'random_path'

    def select(self, graph, src, dst, link_state=None, seed=None, k=DEFAULT_K):
        cands = k_shortest_paths(graph, src, dst, k=k)
        if not cands:
            return None
        rng = random.Random(seed)
        return rng.choice(cands)
