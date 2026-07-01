#!/usr/bin/env python3
"""
S1.7 -- detection + localization experiment (hybrid design; GATE for Phase 2).

Single-process (no Ryu, no background): brings up each topology with live
traffic, injects every inter-switch link fault + a node fault + a congestion
window, and after each injection polls OpenFlow port state
(`ovs-ofctl dump-ports-desc`) to detect the LINK_DOWN and localize the exact
failed link. This is the same port-status signal the production controller
(framework/controller/detect_app.py) consumes as events; polling here keeps the
experiment to one process.

Measures:
  * localization accuracy = injected hard faults whose EXACT failed link was
    identified from port state,
  * detection latency (poll-bounded),
  * congestion false alarms = ports reported down during a congestion window
    (0 by design -- congestion never lowers a port).

PASS (hybrid gate): localization >= 0.90 and congestion false alarms == 0.

Run:  sudo python3 experiments/exp_p1_detection.py
"""
import os
import re
import sys
import time
import argparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'topologies'))

from mininet.log import setLogLevel               # noqa: E402
from topologies import builder, mesh_spec, mesh2_spec  # noqa: E402
from topologies.traffic import TrafficGenerator   # noqa: E402

FAULT_SETTLE = 3.0      # seconds allowed to detect a down link
GAP = 3
# Match ANY port header "N(name):" -- for the non-numeric LOCAL port we set
# cur=None so its always-present LINK_DOWN is ignored (it is the bridge-local
# port, not a real link).
_PORTHDR = re.compile(r'^\s*(\S+?)\((\S+?)\):')


def poll_down_ports(net, spec):
    """Return {(dpid,port)} currently reporting LINK_DOWN, via port state."""
    down = set()
    for s in spec.SWITCHES:
        dpid = spec.sw_to_dpid(s)
        out = net.get(s).cmd('ovs-ofctl -O OpenFlow13 dump-ports-desc %s 2>/dev/null' % s)
        cur = None
        for line in out.splitlines():
            m = _PORTHDR.match(line)
            if m:
                pid = m.group(1)
                cur = int(pid) if pid.isdigit() else None   # LOCAL -> None (ignore)
            elif cur is not None and 'LINK_DOWN' in line:
                down.add((dpid, cur))
                cur = None
    return down


def down_to_links(spec, down):
    """Map a set of down (dpid,port) to the set of failed links {frozenset(sw,sw)}."""
    peers = spec.port_peers()
    links = set()
    for (dpid, port) in down:
        sw = spec.dpid_to_sw(dpid)
        peer = peers.get((sw, port))
        if peer:
            links.add(frozenset({sw, peer[0]}))
    return links


def detect_link(net, spec, expected_link, deadline):
    """Poll until `expected_link` shows down or deadline; return (found, latency)."""
    t0 = time.time()
    while time.time() - t0 < deadline:
        links = down_to_links(spec, poll_down_ports(net, spec))
        if expected_link in links:
            return True, time.time() - t0
        time.sleep(0.4)
    return False, None


def run_topo(spec):
    name = spec.__name__.split('.')[-1]
    net = builder.create_net(spec, stp=True)
    print('*** [%s] STP converging (45s)...' % name)
    net.get('s1').cmd('sleep 45')
    tg = TrafficGenerator(net, seed=7)
    tg.start_mix(dur=400, n_bursts=8)
    net.get('s1').cmd('sleep 6')

    hard_total = loc = 0
    lats = []
    cong_fa = 0

    # every inter-switch link
    for (a, pa, b, pb, _bw, _d) in spec.SWITCH_LINKS:
        link = frozenset({a, b})
        net.configLinkStatus(a, b, 'down')
        hard_total += 1
        found, lat = detect_link(net, spec, link, FAULT_SETTLE)
        if found:
            loc += 1
            lats.append(lat)
            print('   link %s-%s: localized in %.2fs' % (a, b, lat))
        else:
            print('   link %s-%s: MISSED' % (a, b))
        net.configLinkStatus(a, b, 'up')
        time.sleep(GAP)

    # node fault (isolate a mid switch) -> each incident link should localize
    node = spec.SWITCHES[2]
    nbrs = list(spec.build_graph()[node])
    for n in nbrs:
        net.configLinkStatus(node, n, 'down')
    time.sleep(0.5)
    links_now = down_to_links(spec, poll_down_ports(net, spec))
    for n in nbrs:
        hard_total += 1
        if frozenset({node, n}) in links_now:
            loc += 1
    print('   node %s isolated: %d/%d incident links localized'
          % (node, sum(frozenset({node, n}) in links_now for n in nbrs), len(nbrs)))
    for n in nbrs:
        net.configLinkStatus(node, n, 'up')
    time.sleep(GAP)

    # congestion window (soft) -- must NOT report any port down
    net.get('h2').cmd('iperf -s -u -p 5300 >/dev/null 2>&1 &')
    time.sleep(0.3)
    net.get('h1').cmd('iperf -c %s -u -b 40M -t 10 -p 5300 >/dev/null 2>&1 &'
                      % spec.HOST_ATTACH['h2'][3])
    for _ in range(6):
        time.sleep(1.5)
        cong_fa += len(poll_down_ports(net, spec))
    print('   congestion window: %d port-down reports (want 0)' % cong_fa)

    tg.stop()
    net.get('h1').cmd('pkill -f iperf 2>/dev/null')
    net.stop()
    return hard_total, loc, lats, cong_fa


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--topos', default='mesh,mesh2')
    args = ap.parse_args()
    setLogLevel('warning')
    chosen = {'mesh': mesh_spec, 'mesh2': mesh2_spec}
    specs = [chosen[t] for t in args.topos.split(',') if t in chosen]

    print('==== S1.7 DETECTION + LOCALIZATION (hybrid, port-state) ====')
    tot_h = tot_l = tot_fa = 0
    all_lat = []
    per = {}
    for spec in specs:
        h, l, lat, fa = run_topo(spec)
        per[spec.__name__.split('.')[-1]] = (h, l, fa)
        tot_h += h; tot_l += l; tot_fa += fa; all_lat += lat

    print('\n---- summary ----')
    for name, (h, l, fa) in per.items():
        print('%-11s hard=%2d localized=%2d acc=%.3f cong_false_alarm=%d'
              % (name, h, l, l / max(h, 1), fa))
    overall = tot_l / max(tot_h, 1)
    mlat = sum(all_lat) / len(all_lat) if all_lat else float('nan')
    print('OVERALL localization=%.3f (%d/%d) mean_latency=%.2fs congestion_FA=%d'
          % (overall, tot_l, tot_h, mlat, tot_fa))
    ok = overall >= 0.90 and tot_fa == 0
    print('\n*** S1.7 GATE (localization>=0.90 AND congestion FA==0): %s'
          % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
