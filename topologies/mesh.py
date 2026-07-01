#!/usr/bin/env python3
"""
SHIELD framework — big mesh topology (step S1.1).

Goal of this topology: give the framework a substrate on which "find the BEST
recovery path" is a real, non-trivial decision. The single hardcoded backup of
the old reroute_topo.py is replaced by THREE node-disjoint paths between the two
test hosts, each with a DIFFERENT quality profile (short-but-skinny,
balanced, long-but-fat). The Resolver (S2.x) must then pick the best one given
live link state -- something that is impossible when only one backup exists.

    h1 --- s1 ==(A: short, bw5,  2ms)== s2 ===================== s6 --- h2
             \                                                  /
              \=(B: bw10, 5ms)= s3 == s4 =======================/
               \                                               /
                \=(C: fat,  bw20, 10ms)= s5 == s7 ============/

Three internally node-disjoint paths (share only the endpoints s1, s6):
    A : s1 - s2 - s6              (2 switch hops; lowest latency, lowest bw)
    B : s1 - s3 - s4 - s6         (3 hops; balanced)
    C : s1 - s5 - s7 - s6         (3 hops; highest bw, highest latency)

Loop-safety: this graph contains physical loops. In PRODUCTION the switches run
fail-secure and forwarding is done entirely by proactive flows the SHIELD
controller installs (no flooding, static host ARP => no ARP storms). For the
standalone SELF-TEST below we instead enable STP so a plain `pingall` can prove
physical connectivity WITHOUT our controller running.

Explicit port numbering (downstream telemetry/pathing code relies on these):
    s1: 1->h1  2->s2(A)  3->s3(B)  4->s5(C)
    s2: 1->s1  2->s6
    s3: 1->s1  2->s4
    s4: 1->s3  2->s6
    s5: 1->s1  2->s7
    s7: 1->s5  2->s6
    s6: 1->h2  2->s2(A)  3->s4(B)  4->s7(C)

Run the self-test (needs sudo):   sudo python3 topologies/mesh.py --test
Open an interactive CLI:          sudo python3 topologies/mesh.py
"""
import sys

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------
H1_MAC, H1_IP = '00:00:00:00:00:01', '10.0.0.1'
H2_MAC, H2_IP = '00:00:00:00:00:02', '10.0.0.2'

# ---------------------------------------------------------------------------
# Link table.  Each entry: (a, pa, b, pb, bw_mbit, delay_ms)
# Host links are fat/fast so they never bottleneck a path under test.
# ---------------------------------------------------------------------------
HOST_LINKS = [
    ('h1', 0, 's1', 1, 100, 1),
    ('h2', 0, 's6', 1, 100, 1),
]
# Inter-switch links, grouped by path.  The bw/delay asymmetry between paths is
# deliberate -- it is what makes "pick the best path" a meaningful problem.
SWITCH_LINKS = [
    # Path A: short, low latency, low bandwidth
    ('s1', 2, 's2', 1, 5, 2),
    ('s2', 2, 's6', 2, 5, 2),
    # Path B: balanced
    ('s1', 3, 's3', 1, 10, 5),
    ('s3', 2, 's4', 1, 10, 5),
    ('s4', 2, 's6', 3, 10, 5),
    # Path C: long, high latency, high bandwidth
    ('s1', 4, 's5', 1, 20, 10),
    ('s5', 2, 's7', 1, 20, 10),
    ('s7', 2, 's6', 4, 20, 10),
]

SWITCHES = ['s1', 's2', 's3', 's4', 's5', 's6', 's7']

# The three intended host-to-host paths (switch level), for verification/reuse.
INTENDED_PATHS = [
    ['s1', 's2', 's6'],
    ['s1', 's3', 's4', 's6'],
    ['s1', 's5', 's7', 's6'],
]


def build_graph():
    """Return the switch-level adjacency graph with link attributes.

    Shape: { sX: { sY: {'lport': int, 'rport': int, 'bw': int, 'delay': int} } }
    Reused by the telemetry graph (S1.2) and the k-shortest-paths / Resolver
    (S2.x); keeping the topology self-describing avoids a second source of truth.
    """
    g = {s: {} for s in SWITCHES}
    for a, pa, b, pb, bw, delay in SWITCH_LINKS:
        g[a][b] = {'lport': pa, 'rport': pb, 'bw': bw, 'delay': delay}
        g[b][a] = {'lport': pb, 'rport': pa, 'bw': bw, 'delay': delay}
    return g


def node_disjoint_paths(graph, src, dst):
    """Greedy vertex-disjoint path finder (Menger lower bound).

    Repeatedly take a shortest path (BFS), then remove its *intermediate*
    vertices so the next path cannot reuse them.  The number of paths returned
    is a valid lower bound on the max number of internally node-disjoint paths;
    for this designed mesh it recovers all three.
    """
    remaining = {u: set(nbrs) for u, nbrs in graph.items()}
    paths = []
    while True:
        # BFS for a shortest src->dst path in the current (reduced) graph.
        prev, seen, queue = {src: None}, {src}, [src]
        while queue:
            u = queue.pop(0)
            if u == dst:
                break
            for v in remaining.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    prev[v] = u
                    queue.append(v)
        if dst not in prev:
            break
        # Reconstruct.
        path, node = [], dst
        while node is not None:
            path.append(node)
            node = prev[node]
        path.reverse()
        paths.append(path)
        # Remove intermediate vertices of this path from the graph.
        for mid in path[1:-1]:
            for w in remaining.pop(mid, ()):
                remaining[w].discard(mid)
    return paths


def create_net(standalone=False):
    """Build (but do not start CLI on) the mesh.

    standalone=False -> fail-secure switches bound to the remote SHIELD
                        controller (production; controller installs all flows).
    standalone=True  -> switches act as normal L2 bridges with STP, so the
                        topology can be connectivity-tested WITHOUT a controller.
    """
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch,
                  link=TCLink, autoSetMacs=False, build=False)

    c0 = net.addController('c0', ip='127.0.0.1', port=6653)

    h1 = net.addHost('h1', ip=H1_IP + '/24', mac=H1_MAC)
    h2 = net.addHost('h2', ip=H2_IP + '/24', mac=H2_MAC)

    for i, s in enumerate(SWITCHES, start=1):
        net.addSwitch(s, dpid='%016x' % i, protocols='OpenFlow13')

    for a, pa, b, pb, bw, delay in HOST_LINKS + SWITCH_LINKS:
        net.addLink(a, b, port1=pa, port2=pb, bw=bw, delay='%dms' % delay)

    net.build()
    c0.start()

    for s in SWITCHES:
        sw = net.get(s)
        sw.start([c0])
        if standalone:
            # No SHIELD controller in the self-test: behave as a plain learning
            # switch and let STP break the loops so pingall works.
            sw.cmd('ovs-vsctl set-fail-mode %s standalone' % s)
            sw.cmd('ovs-vsctl set bridge %s stp_enable=true' % s)

    # Static ARP: no ARP flooding is ever required => loop-safe under proactive
    # flows, and faster to converge in the self-test.
    h1.cmd('arp -s %s %s' % (H2_IP, H2_MAC))
    h2.cmd('arp -s %s %s' % (H1_IP, H1_MAC))
    return net


def self_test():
    """S1.1 PASS gate: full connectivity + >=3 node-disjoint paths confirmed."""
    ok = True

    # (1) Structural check: >=3 internally node-disjoint paths in the graph.
    g = build_graph()
    disjoint = node_disjoint_paths(g, 's1', 's6')
    info('*** Node-disjoint s1->s6 paths found: %d\n' % len(disjoint))
    for p in disjoint:
        info('      %s\n' % ' - '.join(p))
    if len(disjoint) < 3:
        info('!!! FAIL: expected >=3 disjoint paths, got %d\n' % len(disjoint))
        ok = False

    # (2) Live connectivity check on the real Mininet net (standalone + STP).
    net = create_net(standalone=True)
    info('*** Waiting for STP to converge...\n')
    # STP needs ~a few forwarding-delay cycles to unblock ports.
    net.get('s1').cmd('sleep 45')
    info('*** pingall\n')
    drop = net.pingAll()
    net.stop()
    if drop != 0.0:
        info('!!! FAIL: pingall drop = %.1f%% (want 0%%)\n' % drop)
        ok = False

    info('\n*** S1.1 SELF-TEST: %s\n' % ('PASS' if ok else 'FAIL'))
    return ok


def write_diagram():
    """Emit a Graphviz DOT + ASCII diagram to results/figures/ (no matplotlib)."""
    import os
    outdir = os.path.join(os.path.dirname(__file__), '..', 'results', 'figures')
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    dot = ['graph mesh {', '  rankdir=LR; node [shape=circle];',
           '  h1 [shape=box]; h2 [shape=box];']
    for a, _, b, _, bw, delay in HOST_LINKS:
        dot.append('  %s -- %s [label="%dms"];' % (a, b, delay))
    for a, _, b, _, bw, delay in SWITCH_LINKS:
        dot.append('  %s -- %s [label="%dM/%dms"];' % (a, b, bw, delay))
    dot.append('}')
    with open(os.path.join(outdir, 'mesh_topology.dot'), 'w') as f:
        f.write('\n'.join(dot) + '\n')
    print('wrote', os.path.join(outdir, 'mesh_topology.dot'))


if __name__ == '__main__':
    setLogLevel('info')
    if '--diagram' in sys.argv:
        write_diagram()
    elif '--test' in sys.argv:
        ok = self_test()
        write_diagram()
        sys.exit(0 if ok else 1)
    else:
        net = create_net(standalone=False)
        info('*** Mesh up (fail-secure, bound to remote controller @6653).\n'
             '*** Start the SHIELD controller first, then use this CLI.\n')
        CLI(net)
        net.stop()
