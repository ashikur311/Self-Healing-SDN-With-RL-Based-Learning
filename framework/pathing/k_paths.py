#!/usr/bin/env python3
"""
SHIELD candidate-path generation (step S2.1) — Yen's k-shortest-paths.

This is the first half of the Resolver: given the live topology graph (with the
failed element already removed) it enumerates the k best simple paths between two
switches, ordered by a link cost (default = latency/delay). S2.2's resolver then
*scores* these candidates on the full QoS objective and picks one to install.

Design notes
------------
* Operates on the adjacency shape produced by
  `framework.telemetry.topology_graph.TopologyGraph.active_graph()`:
      { sX: { sY: {'lport':int, 'rport':int, 'bw':int, 'delay':int} } }
  ...which is switch-name keyed and symmetric. The same shape is produced by
  `topologies.mesh_spec.build_graph()`, so this module is testable offline with
  no Mininet/controller.
* Dijkstra (heap-based) is the inner shortest-path; Yen's loop deviates around
  each already-found path to get the next-shortest simple path.
* The failed link/node is excluded by the CALLER via `active_graph(exclude_...)`,
  or directly here through `candidate_paths(..., failed_node=, failed_edge=)`.

Everything is switch-name keyed (the graph's own convention). The actuation layer
maps a chosen path back to per-switch out-ports via the 'lport'/'rport' attrs.
"""
import heapq
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# -- edge cost functions -----------------------------------------------------
# Signature: weight(a, b, attr) -> number. Kept pluggable so the k-ordering can
# be latency (default), hop count, or anything the Resolver wants to pre-sort by.
def by_delay(a, b, attr):
    """Latency cost — the natural ordering for 'shortest recovery path'."""
    return attr.get('delay', 1)


def by_hops(a, b, attr):
    """Unit cost — orders purely by number of hops."""
    return 1


# -- inner shortest path -----------------------------------------------------
def dijkstra(graph, src, dst, weight=by_delay, banned_nodes=None, banned_edges=None):
    """Shortest path src->dst by `weight`, honouring banned nodes/edges.

    `banned_edges` is a set of frozenset({u, v}). Returns the node list
    [src, ..., dst] or None if dst is unreachable.
    """
    banned_nodes = set(banned_nodes or ())
    banned_edges = {frozenset(e) for e in (banned_edges or ())}
    if src in banned_nodes or dst in banned_nodes or src not in graph:
        return None
    if src == dst:
        return [src]

    dist = {src: 0}
    prev = {}
    visited = set()
    pq = [(0, src)]                       # (cost-so-far, node)
    while pq:
        d, u = heapq.heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        if u == dst:
            break
        for v, attr in graph.get(u, {}).items():
            if v in banned_nodes or v in visited:
                continue
            if frozenset({u, v}) in banned_edges:
                continue
            nd = d + weight(u, v, attr)
            if nd < dist.get(v, float('inf')):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    if dst not in dist:
        return None
    path, node = [], dst
    while node != src:
        path.append(node)
        node = prev[node]
    path.append(src)
    path.reverse()
    return path


def path_cost(graph, path, weight=by_delay):
    """Total edge cost of a node-list path under `weight`."""
    total = 0
    for a, b in zip(path, path[1:]):
        total += weight(a, b, graph[a][b])
    return total


# -- Yen's k-shortest-paths --------------------------------------------------
def k_shortest_paths(graph, src, dst, k=3, weight=by_delay):
    """Return up to `k` simple paths src->dst, ascending by total `weight`.

    Fewer than k paths are returned when the graph does not admit that many
    (e.g. after excluding a failed element). Each path is a node list
    [src, ..., dst]. Order is deterministic: cost first, then the path itself.
    """
    if k <= 0:
        return []
    first = dijkstra(graph, src, dst, weight)
    if first is None:
        return []
    accepted = [first]
    # candidate heap of (cost, path); path (list) breaks cost ties deterministically.
    candidates = []
    seen = {tuple(first)}

    while len(accepted) < k:
        prev = accepted[-1]
        for i in range(len(prev) - 1):
            spur_node = prev[i]
            root_path = prev[:i + 1]

            # Ban edges that would recreate an already-accepted path sharing this root.
            banned_edges = set()
            for p in accepted:
                if len(p) > i and p[:i + 1] == root_path:
                    banned_edges.add(frozenset({p[i], p[i + 1]}))
            # Ban the root's interior nodes so the spur path stays simple.
            banned_nodes = set(root_path[:-1])

            spur = dijkstra(graph, spur_node, dst, weight,
                            banned_nodes=banned_nodes, banned_edges=banned_edges)
            if spur is None:
                continue
            total = root_path[:-1] + spur
            key = tuple(total)
            if key in seen:
                continue
            seen.add(key)
            heapq.heappush(candidates, (path_cost(graph, total, weight), total))

        if not candidates:
            break
        _, best = heapq.heappop(candidates)
        accepted.append(best)

    return accepted[:k]


# -- convenience over a live TopologyGraph -----------------------------------
def path_metrics(graph, path):
    """Aggregate QoS-relevant attributes of a path (input to S2.2 scoring).

    Returns {'hops', 'delay' (sum), 'bw' (bottleneck/min), 'path'}.
    """
    delay = sum(graph[a][b]['delay'] for a, b in zip(path, path[1:]))
    bw = min((graph[a][b]['bw'] for a, b in zip(path, path[1:])), default=0)
    return {'hops': len(path) - 1, 'delay': delay, 'bw': bw, 'path': path}


def candidate_paths(topo_graph, src, dst, k=3, weight=by_delay,
                    failed_node=None, failed_edge=None):
    """k-shortest paths on a live TopologyGraph with the failed element removed.

    `topo_graph` is a `TopologyGraph`; `failed_node` a switch name; `failed_edge`
    a pair of switch names. Down links already tracked by the graph are excluded
    automatically via active_graph().
    """
    g = topo_graph.active_graph(
        exclude_nodes=[failed_node] if failed_node else None,
        exclude_edges=[failed_edge] if failed_edge else None)
    return k_shortest_paths(g, src, dst, k=k, weight=weight)


if __name__ == '__main__':
    # Offline demo on the real mesh spec (no controller needed).
    from topologies.mesh_spec import build_graph
    g = build_graph()
    print("[k_paths] 3-shortest s1->s6 (by delay):")
    for p in k_shortest_paths(g, 's1', 's6', k=3):
        print("   %-24s cost=%d  %s" % (
            '->'.join(p), path_cost(g, p), path_metrics(g, p)))
