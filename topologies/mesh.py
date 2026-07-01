#!/usr/bin/env python3
"""
SHIELD framework — big mesh topology BUILDER (step S1.1).

The graph/spec (switch & link tables, path helpers) lives in the Mininet-free
`mesh_spec.py` so the control plane can import it too. This file only builds the
live Mininet network from that spec.

Goal of this topology: give the framework a substrate on which "find the BEST
recovery path" is a real, non-trivial decision. The single hardcoded backup of
the old reroute_topo.py is replaced by THREE node-disjoint paths between the two
test hosts, each with a DIFFERENT quality profile.

    h1 --- s1 ==(A: short, bw5,  2ms)== s2 ===================== s6 --- h2
             \                                                  /
              \=(B: bw10, 5ms)= s3 == s4 =======================/
               \                                               /
                \=(C: fat,  bw20, 10ms)= s5 == s7 ============/

Loop-safety: physical loops exist. In PRODUCTION the switches run fail-secure and
the SHIELD controller installs all forwarding flows (static host ARP => no ARP
storms). For the standalone SELF-TEST / telemetry test we enable STP so plain
forwarding works without our path logic.

Run the self-test (needs sudo):   sudo python3 topologies/mesh.py --test
STP mode (for telemetry test):    sudo python3 topologies/mesh.py --stp
Interactive CLI (production):      sudo python3 topologies/mesh.py
"""
import sys

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

from mesh_spec import (SWITCHES, HOST_LINKS, SWITCH_LINKS, HOST_ATTACH,
                       H1_IP, H1_MAC, H2_IP, H2_MAC,
                       build_graph, node_disjoint_paths, sw_to_dpid)


def _enable_stp(net):
    for s in SWITCHES:
        sw = net.get(s)
        sw.cmd('ovs-vsctl set-fail-mode %s standalone' % s)
        sw.cmd('ovs-vsctl set bridge %s stp_enable=true' % s)


def create_net(stp=False):
    """Build (but do not start CLI on) the mesh.

    stp=False -> fail-secure switches bound to the remote SHIELD controller
                 (production; controller installs all flows).
    stp=True  -> standalone L2 bridges with STP, so the topology can be
                 connectivity/telemetry-tested WITHOUT bespoke path logic.
    """
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch,
                  link=TCLink, autoSetMacs=False, build=False)

    c0 = net.addController('c0', ip='127.0.0.1', port=6653)

    net.addHost('h1', ip=H1_IP + '/24', mac=H1_MAC)
    net.addHost('h2', ip=H2_IP + '/24', mac=H2_MAC)

    for s in SWITCHES:
        net.addSwitch(s, dpid='%016x' % sw_to_dpid(s), protocols='OpenFlow13')

    for a, pa, b, pb, bw, delay in HOST_LINKS + SWITCH_LINKS:
        net.addLink(a, b, port1=pa, port2=pb, bw=bw, delay='%dms' % delay)

    net.build()
    c0.start()
    for s in SWITCHES:
        net.get(s).start([c0])
    if stp:
        _enable_stp(net)

    # Static ARP => no ARP flooding ever required (loop-safe under proactive flows).
    h1, h2 = net.get('h1'), net.get('h2')
    h1.cmd('arp -s %s %s' % (H2_IP, H2_MAC))
    h2.cmd('arp -s %s %s' % (H1_IP, H1_MAC))
    return net


def self_test():
    """S1.1 PASS gate: full connectivity + >=3 node-disjoint paths confirmed."""
    ok = True

    g = build_graph()
    disjoint = node_disjoint_paths(g, 's1', 's6')
    info('*** Node-disjoint s1->s6 paths found: %d\n' % len(disjoint))
    for p in disjoint:
        info('      %s\n' % ' - '.join(p))
    if len(disjoint) < 3:
        info('!!! FAIL: expected >=3 disjoint paths, got %d\n' % len(disjoint))
        ok = False

    net = create_net(stp=True)
    info('*** Waiting for STP to converge...\n')
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
    """Emit a Graphviz DOT diagram to results/figures/ (no matplotlib needed)."""
    import os
    outdir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..',
                                          'results', 'figures'))
    os.makedirs(outdir, exist_ok=True)
    dot = ['graph mesh {', '  rankdir=LR; node [shape=circle];',
           '  h1 [shape=box]; h2 [shape=box];']
    for a, _, b, _, bw, delay in HOST_LINKS:
        dot.append('  %s -- %s [label="%dms"];' % (a, b, delay))
    for a, _, b, _, bw, delay in SWITCH_LINKS:
        dot.append('  %s -- %s [label="%dM/%dms"];' % (a, b, bw, delay))
    dot.append('}')
    path = os.path.join(outdir, 'mesh_topology.dot')
    with open(path, 'w') as f:
        f.write('\n'.join(dot) + '\n')
    print('wrote', path)


if __name__ == '__main__':
    setLogLevel('info')
    if '--diagram' in sys.argv:
        write_diagram()
    elif '--test' in sys.argv:
        ok = self_test()
        write_diagram()
        sys.exit(0 if ok else 1)
    elif '--stp' in sys.argv:
        net = create_net(stp=True)
        info('*** Mesh up (standalone + STP). Controller may still poll stats.\n')
        CLI(net)
        net.stop()
    else:
        net = create_net(stp=False)
        info('*** Mesh up (fail-secure, bound to remote controller @6653).\n'
             '*** Start the SHIELD controller first, then use this CLI.\n')
        CLI(net)
        net.stop()
