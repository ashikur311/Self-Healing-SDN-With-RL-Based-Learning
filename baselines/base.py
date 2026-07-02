#!/usr/bin/env python3
"""
Shared plumbing for the S2.3 recovery baselines.

Every baseline is a path *selector* with the same contract as the SHIELD Resolver
so the S2.5 experiment can drive them all through one loop:

    selector.select(graph, src, dst, link_state=None, seed=None, k=K) -> path|None

`graph` is the live active-graph adjacency (failed element already removed), so a
returned path is by construction fault-avoiding. `link_state` is the *true* per-link
loss/quality map; only the oracle is allowed to look at it (it has full knowledge).
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DEFAULT_K = 3


def path_is_viable(graph, path):
    """True if every hop of `path` still exists in `graph` (endpoints included)."""
    if not path or path[0] not in graph:
        return False
    for a, b in zip(path, path[1:]):
        if b not in graph.get(a, {}):
            return False
    return True
