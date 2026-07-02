#!/usr/bin/env python3
"""
Baseline: oracle path (upper bound).

The oracle has FULL knowledge -- it runs the exact same scoring objective as the
SHIELD Resolver but with the *true* per-link state (real loss/quality), so it is
the best achievable choice under that objective. SHIELD, by contrast, only sees
estimated/observed telemetry. The oracle is therefore the ceiling: the S2.5 PASS
gate asks SHIELD to land within ~10% of it. When SHIELD == oracle, the framework's
observation is good enough that no better selection exists.
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.agents.resolver import Resolver
from baselines.base import DEFAULT_K


class OraclePath:
    name = 'oracle_path'

    def __init__(self, weights=None):
        # Same objective/weights as the deployed Resolver -- the difference is
        # purely the quality of the link_state it is given.
        self._resolver = Resolver(weights)

    def select(self, graph, src, dst, link_state=None, seed=None, k=DEFAULT_K):
        best = self._resolver.select(graph, src, dst, k=k, link_state=link_state)
        return best['path'] if best else None
