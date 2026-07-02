#!/usr/bin/env python3
"""
S2.4 -- closed-loop recovery integration test on the big mesh.

Proves the PASS gate: "inject a fault -> auto-detect -> resolve -> recover; pingall
recovers." Single-process (the robust pattern in this sandbox, as in S1.7): it
brings up the mesh fail-secure, installs the SHIELD PRIMARY path, then for each
scenario injects a fault, DETECTS the down link from OpenFlow port-state (the same
signal shield_app.py consumes as an event), asks the shared ShieldCore brain to
RESOLVE the best surviving path, ACTUATES it via ovs-ofctl, and re-measures ping.
On heal it verifies FAILBACK to the primary.

The decision logic (localize->resolve->plan) is exactly ShieldCore -- the same code
the production Ryu app (framework/controller/shield_app.py) runs; only the actuator
differs (ovs-ofctl here, OFPFlowMod there).

Run:  sudo python3 -u experiments/exp_p2_recovery.py > results/logs/s2_4_recovery.log 2>&1
"""
import os
import re
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'topologies'))

from mininet.log import setLogLevel                                   # noqa: E402
from topologies import builder, mesh_spec                            # noqa: E402
from framework.controller.shield_core import (ShieldCore,            # noqa: E402
                                              PRIO_PRIMARY, PRIO_BACKUP)

SETTLE = 2.0
DETECT_DEADLINE = 4.0
_PORTHDR = re.compile(r'^\s*(\S+?)\((\S+?)\):')


# -- ovs-ofctl actuator (the only difference from the Ryu OFPFlowMod path) ----
def _flow_match(entry):
    return 'dl_dst=%s' % entry['eth_dst']


def install(net, entries, priority):
    for e in entries:
        sw = 's%d' % e['dpid']
        net.get(sw).cmd('ovs-ofctl -O OpenFlow13 add-flow %s '
                        '"priority=%d,%s,actions=output:%d"'
                        % (sw, priority, _flow_match(e), e['out_port']))


def delete(net, entries, priority):
    for e in entries:
        sw = 's%d' % e['dpid']
        net.get(sw).cmd('ovs-ofctl -O OpenFlow13 --strict del-flows %s '
                        '"priority=%d,%s"' % (sw, priority, _flow_match(e)))


def apply_delta(net, delta):
    if not delta:
        return
    install(net, delta.get('install', []), delta['priority'])
    delete(net, delta.get('delete', []), delta['priority'])


# -- detection (poll OF port-state, same signal shield_app consumes) ----------
def down_links(net, spec):
    peers = spec.port_peers()
    links = set()
    for s in spec.SWITCHES:
        out = net.get(s).cmd('ovs-ofctl -O OpenFlow13 dump-ports-desc %s '
                             '2>/dev/null' % s)
        cur = None
        for line in out.splitlines():
            m = _PORTHDR.match(line)
            if m:
                pid = m.group(1)
                cur = int(pid) if pid.isdigit() else None
            elif cur is not None and 'LINK_DOWN' in line:
                peer = peers.get((s, cur))
                if peer:
                    links.add(frozenset({s, peer[0]}))
                cur = None
    return links


def detect(net, spec, expected, deadline):
    t0 = time.time()
    while time.time() - t0 < deadline:
        if expected <= down_links(net, spec):
            return True, time.time() - t0
        time.sleep(0.3)
    return False, None


# -- measurement --------------------------------------------------------------
def ping_loss(net, count=6):
    h1, h2 = net.get('h1'), net.get('h2')
    out = h1.cmd('ping -c %d -i 0.3 -W 1 %s' % (count, mesh_spec.H2_IP))
    m = re.search(r'(\d+)% packet loss', out)
    return int(m.group(1)) if m else 100


def scenario(net, core, links, label):
    """Inject `links` (list of (a,b)) down, detect+resolve+actuate, verify recover,
    then heal + verify failback. Returns True on full success."""
    print('\n=== scenario: %s ===' % label)
    print('   pre-fault ping loss: %d%% (active=%s)'
          % (ping_loss(net), '->'.join(core.active)))

    for (a, b) in links:
        net.configLinkStatus(a, b, 'down')
    expected = {frozenset({a, b}) for (a, b) in links}
    found, lat = detect(net, mesh_spec, expected, DETECT_DEADLINE)
    if not found:
        print('   !! FAIL: fault not detected')
        return False
    for (a, b) in links:
        core.mark_down(a, b)
    delta = core.plan_recovery()
    apply_delta(net, delta)
    print('   detected in %.2fs -> %s to %s'
          % (lat, delta['action'], '->'.join(delta['path'])))
    time.sleep(SETTLE)
    post = ping_loss(net)
    print('   post-recovery ping loss: %d%% (active=%s)'
          % (post, '->'.join(core.active)))
    recovered = post == 0

    # heal + failback
    for (a, b) in links:
        net.configLinkStatus(a, b, 'up')
    time.sleep(SETTLE)
    for (a, b) in links:
        core.mark_up(a, b)
    fb = core.plan_recovery()
    apply_delta(net, fb)
    fb_ok = fb is not None and fb['action'] == 'failback'
    time.sleep(SETTLE)
    post_fb = ping_loss(net)
    print('   after heal: %s, ping loss: %d%% (active=%s)'
          % (fb['action'] if fb else 'none', post_fb, '->'.join(core.active)))
    return recovered and fb_ok and post_fb == 0


def main():
    setLogLevel('warning')
    os.system('mn -c >/dev/null 2>&1')
    net = builder.create_net(mesh_spec, stp=False)
    ok = True
    try:
        # Fail-secure: only SHIELD's explicit flows forward (loop-safe under the
        # physical mesh loops). Then install the PRIMARY path proactively.
        for s in mesh_spec.SWITCHES:
            net.get(s).cmd('ovs-vsctl set-fail-mode %s secure' % s)
        core = ShieldCore()
        install(net, core.initial_plan(), PRIO_PRIMARY)
        time.sleep(SETTLE)
        base = ping_loss(net)
        print('*** primary=%s installed; baseline ping loss: %d%%'
              % ('->'.join(core.primary), base))
        if base != 0:
            print('!! FAIL: no baseline connectivity on primary')
            ok = False

        # Scenario 1: primary-link fault -> reroute -> heal -> failback.
        ok &= scenario(net, core, [('s1', 's2')], 'primary link s1-s2 down')
        # Scenario 2: node isolation (both Path-A links) -> reroute avoids s2.
        ok &= scenario(net, core, [('s1', 's2'), ('s2', 's6')], 'node s2 isolated')
    finally:
        net.stop()
        os.system('mn -c >/dev/null 2>&1')

    print('\n*** S2.4 GATE (auto detect->resolve->recover, pingall recovers): %s'
          % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
