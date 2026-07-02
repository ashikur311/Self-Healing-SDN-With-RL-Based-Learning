#!/usr/bin/env python3
"""
SHIELD Resolver (step S2.2) — score candidate paths and pick the BEST recovery.

This is the headline "find the best solution" component. S2.1 (k_paths) enumerates
the k shortest simple paths around the failed element; the Resolver *scores* each
one on a QoS objective and selects the winner, then emits a flow plan (per-switch
out-ports) for the actuation layer to install via OFPFlowMod.

Objective
---------
    score = w_lat*(-latency) + w_cap*(capacity) + w_loss*(-loss) + w_hops*(-hops)

The four raw metrics live on different scales (ms vs Mbit/s vs fraction vs count),
so each is min-max normalized to a [0,1] "goodness" across the candidate set before
weighting. This makes the weights interpretable as *relative priorities* and the
choice scale-free: a latency-priority operator and a bandwidth-priority operator
provably pick different paths from the same candidates (see the unit test).

Inputs
------
* Latency  = summed link delay along the path        (lower better)
* Capacity = bottleneck (min) bandwidth along the path (higher better)
* Loss     = worst per-link loss fraction along the path, from live link-state
             (default 0 when telemetry has no loss signal)  (lower better)
* Hops     = number of links                          (lower better)

Everything is switch-name keyed (the graph's convention). `flow_plan()` turns the
chosen switch-level path into concrete (dpid, eth_dst, out_port) entries for both
directions, reusing the port attrs in the graph and HOST_ATTACH from the spec.
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.pathing.k_paths import k_shortest_paths, path_metrics

# Relative priorities. Latency/capacity/loss are the primary QoS levers; hops is a
# mild tie-breaker (prefer simpler paths, all else equal).
DEFAULT_WEIGHTS = {'latency': 1.0, 'capacity': 1.0, 'loss': 1.0, 'hops': 0.25}


def _link_loss(link_state, a, b):
    """Loss fraction on edge {a,b} from an optional link-state map; 0 if absent."""
    if not link_state:
        return 0.0
    return float(link_state.get(frozenset({a, b}), 0.0))


def path_qos(graph, path, link_state=None):
    """Raw QoS metrics for one path: latency, capacity, loss, hops."""
    m = path_metrics(graph, path)                       # hops, delay(sum), bw(min)
    loss = max((_link_loss(link_state, a, b) for a, b in zip(path, path[1:])),
               default=0.0)
    return {'path': path, 'latency': m['delay'], 'capacity': m['bw'],
            'loss': loss, 'hops': m['hops']}


def _normalize(values, higher_is_better):
    """Min-max map to [0,1] goodness. All-equal -> all 1.0 (dimension is neutral)."""
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    if higher_is_better:
        return [(v - lo) / (hi - lo) for v in values]
    return [(hi - v) / (hi - lo) for v in values]


class Resolver:
    def __init__(self, weights=None):
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)

    # -- scoring -------------------------------------------------------------
    def score(self, qos_list):
        """Composite score per candidate (higher = better). qos_list from path_qos."""
        if not qos_list:
            return []
        good_lat = _normalize([q['latency'] for q in qos_list], higher_is_better=False)
        good_cap = _normalize([q['capacity'] for q in qos_list], higher_is_better=True)
        good_loss = _normalize([q['loss'] for q in qos_list], higher_is_better=False)
        good_hops = _normalize([q['hops'] for q in qos_list], higher_is_better=False)
        w = self.weights
        out = []
        for q, gl, gc, gs, gh in zip(qos_list, good_lat, good_cap, good_loss, good_hops):
            s = (w['latency'] * gl + w['capacity'] * gc +
                 w['loss'] * gs + w['hops'] * gh)
            out.append(dict(q, score=s))
        return out

    # -- selection -----------------------------------------------------------
    def select(self, graph, src, dst, k=3, link_state=None):
        """Return the best candidate dict (with 'score' and 'ranked' list), or None.

        Deterministic tie-break: higher score, then lower latency, then path tuple.
        """
        candidates = k_shortest_paths(graph, src, dst, k=k)
        if not candidates:
            return None
        scored = self.score([path_qos(graph, p, link_state) for p in candidates])
        scored.sort(key=lambda q: (-q['score'], q['latency'], tuple(q['path'])))
        best = dict(scored[0])
        best['ranked'] = scored
        return best


# -- actuation hand-off ------------------------------------------------------
def flow_plan(graph, path, src_host='h1', dst_host='h2'):
    """Per-switch flow entries to realise `path` (switch-level [src..dst]).

    Returns a list of dicts {dpid, eth_dst, out_port, dir}: forward entries steer
    dst_host's MAC toward dst_host, reverse entries steer src_host's MAC toward
    src_host. This is exactly what the actuation layer installs via OFPFlowMod
    (one OFPActionOutput(out_port) per switch). Ports come from the graph's
    'lport' attrs (a's port toward b) and HOST_ATTACH for the edge switches.
    """
    from topologies.mesh_spec import HOST_ATTACH, sw_to_dpid

    src_sw, src_port, src_mac, _ = HOST_ATTACH[src_host]
    dst_sw, dst_port, dst_mac, _ = HOST_ATTACH[dst_host]
    assert path[0] == src_sw and path[-1] == dst_sw, \
        "path endpoints %s..%s != host switches %s..%s" % (
            path[0], path[-1], src_sw, dst_sw)

    plan = []
    # forward: to dst_mac. Interior switches out toward next hop; last out to host.
    for i, sw in enumerate(path):
        if i < len(path) - 1:
            out_port = graph[sw][path[i + 1]]['lport']
        else:
            out_port = dst_port
        plan.append({'dpid': sw_to_dpid(sw), 'eth_dst': dst_mac,
                     'out_port': out_port, 'dir': 'fwd'})
    # reverse: to src_mac, along the reversed path.
    rev = list(reversed(path))
    for i, sw in enumerate(rev):
        if i < len(rev) - 1:
            out_port = graph[sw][rev[i + 1]]['lport']
        else:
            out_port = src_port
        plan.append({'dpid': sw_to_dpid(sw), 'eth_dst': src_mac,
                     'out_port': out_port, 'dir': 'rev'})
    return plan


if __name__ == '__main__':
    # Offline demo on the real mesh spec (no controller needed).
    from topologies.mesh_spec import build_graph
    g = build_graph()
    r = Resolver()
    best = r.select(g, 's1', 's6', k=3)
    print("[resolver] default weights -> best = %s (score %.3f)" % (
        '->'.join(best['path']), best['score']))
    for q in best['ranked']:
        print("   %-22s lat=%2d cap=%2d loss=%.2f hops=%d  score=%.3f" % (
            '->'.join(q['path']), q['latency'], q['capacity'],
            q['loss'], q['hops'], q['score']))
    print("[resolver] flow plan for best:")
    for e in flow_plan(g, best['path']):
        print("   s%d  eth_dst=%s  out_port=%d  (%s)" % (
            e['dpid'], e['eth_dst'], e['out_port'], e['dir']))
