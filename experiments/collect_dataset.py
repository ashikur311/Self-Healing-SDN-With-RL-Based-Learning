#!/usr/bin/env python3
"""
S1.5 -- labeled telemetry data collection at scale.

Runs the full stack together (topology + live traffic + scheduled faults) and,
every POLL seconds, polls each switch's port counters via `ovs-ofctl dump-ports`,
turns them into [throughput, loss, delay] with TelemetryCollector, and writes one
labeled row per (switch, port) to a growing CSV.

Ground truth is exact because faults are injected and labeled from the SAME
process: each row carries
    scenario  in {normal, congestion, link, node, flap}
    is_fault  = 1 iff this port belongs to the actually-failed element, else 0
Congestion rows are anomalous telemetry with is_fault=0 -- the hard negative that
stops the detector from firing on load.

Runs two structurally-different topologies (mesh, mesh2) so S1.7 can train on one
and test on the unseen one.

    sudo python3 experiments/collect_dataset.py                 # both topologies
    sudo python3 experiments/collect_dataset.py --quick         # short smoke run
"""
import os
import re
import sys
import csv
import time
import random
import argparse
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'topologies'))

from mininet.log import setLogLevel, info      # noqa: E402
from topologies import builder                  # noqa: E402
from topologies import mesh_spec, mesh2_spec    # noqa: E402
from topologies.traffic import TrafficGenerator  # noqa: E402
from framework.telemetry.collector import TelemetryCollector  # noqa: E402

POLL = 2.0            # seconds between telemetry polls
FAULT_DUR = 8         # seconds a fault stays active
GAP = 5               # normal seconds between faults
N_LINK_FAULTS = 5     # distinct link faults per topology
CONG_DUR = 12
CONG_TH_KBPS = 200.0  # a port carrying > this during a congestion window = congested
DATASET = os.path.join(ROOT, 'data', 'datasets', 'telemetry_v2.csv')

_PORT_RE = re.compile(r'port\s+"?(\S+?)"?:\s+rx\s+pkts=(\d+|\?),\s+bytes=(\d+|\?),'
                      r'\s+drop=(\d+|\?),\s+errs=(\d+|\?)')
_TX_RE = re.compile(r'tx\s+pkts=(\d+|\?),\s+bytes=(\d+|\?),\s+drop=(\d+|\?),\s+errs=(\d+|\?)')


def _num(x):
    return int(x) if str(x).isdigit() else 0


class _Stat:
    """Duck-typed like ryu OFPPortStats so TelemetryCollector can ingest it."""
    __slots__ = ('port_no', 'rx_bytes', 'tx_bytes', 'rx_packets', 'tx_packets',
                 'rx_dropped', 'tx_dropped', 'rx_errors', 'tx_errors')


def parse_dump_ports(text):
    """Parse `ovs-ofctl -O OpenFlow13 dump-ports` output into _Stat objects."""
    stats, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        m = _PORT_RE.search(line)
        if not m:
            continue
        pid = m.group(1)
        # This OVS prints interface names ("sX-ethN"); the OF port number is N
        # (Mininet names sX-eth<port>). Fall back to a numeric id if present.
        if pid == 'LOCAL':
            continue
        nm = re.search(r'eth(\d+)$', pid)
        if nm:
            port_no = int(nm.group(1))
        elif pid.isdigit():
            port_no = int(pid)
        else:
            continue
        tx = _TX_RE.search(lines[i + 1]) if i + 1 < len(lines) else None
        s = _Stat()
        s.port_no = port_no
        s.rx_packets, s.rx_bytes, s.rx_dropped, s.rx_errors = map(_num, m.groups()[1:])
        if tx:
            s.tx_packets, s.tx_bytes, s.tx_dropped, s.tx_errors = map(_num, tx.groups())
        else:
            s.tx_packets = s.tx_bytes = s.tx_dropped = s.tx_errors = 0
        stats.append(s)
    return stats


def link_ports(spec, a, b):
    """The (dpid,port) pair on both ends of link a-b."""
    e = spec.build_graph()[a][b]
    return {(spec.sw_to_dpid(a), e['lport']), (spec.sw_to_dpid(b), e['rport'])}


def build_plan(spec, rng):
    """Timed fault plan + matching label intervals, derived from the spec.

    Returns (events, total_dur). Each event: (t, fn) where fn mutates the live
    net and the `active` label list. Faults are spaced by GAP normal seconds.
    """
    links = list(spec.SWITCH_LINKS)
    rng.shuffle(links)
    chosen = links[:N_LINK_FAULTS]
    switches_mid = [s for s in spec.SWITCHES
                    if s not in ('s1', spec.SWITCHES[-1])]  # avoid edge switches
    node = rng.choice(switches_mid)
    flap_link = rng.choice(links)

    plan = []          # list of dicts describing each fault phase
    t = GAP            # initial normal warmup
    for (a, pa, b, pb, _bw, _d) in chosen:
        plan.append({'t': t, 'dur': FAULT_DUR, 'kind': 'link',
                     'pair': (a, b), 'ports': link_ports(spec, a, b)})
        t += FAULT_DUR + GAP
    # node fault (isolate switch)
    nbrs = list(spec.build_graph()[node])
    nports = set().union(*(link_ports(spec, node, n) for n in nbrs))
    plan.append({'t': t, 'dur': FAULT_DUR, 'kind': 'node',
                 'node': node, 'nbrs': nbrs, 'ports': nports})
    t += FAULT_DUR + GAP
    # congestion (soft)
    plan.append({'t': t, 'dur': CONG_DUR, 'kind': 'congestion', 'ports': set()})
    t += CONG_DUR + GAP
    # flap
    fa, _fpa, fb = flap_link[0], flap_link[1], flap_link[2]
    plan.append({'t': t, 'dur': FAULT_DUR, 'kind': 'flap',
                 'pair': (fa, fb), 'ports': link_ports(spec, fa, fb)})
    t += FAULT_DUR + GAP
    return plan, t + GAP


def collect_one(spec, seed, writer, stats, quick):
    rng = random.Random(seed)
    net = builder.create_net(spec, stp=True)
    topo_name = spec.__name__.split('.')[-1]
    info('*** [%s seed=%d] STP converging (45s)...\n' % (topo_name, seed))
    net.get('s1').cmd('sleep 45')

    plan, total = build_plan(spec, rng)
    if quick:
        plan, total = plan[:2], plan[1]['t'] + plan[1]['dur'] + GAP

    tg = TrafficGenerator(net, seed=seed)
    tg.start_mix(dur=int(total) + 5, n_bursts=8)

    collector = TelemetryCollector()
    topo = spec.__name__.split('.')[-1]
    active = []                 # currently-active label intervals
    cong_flood_ports = 5200

    def apply_phase(ph):
        if ph['kind'] == 'link':
            net.configLinkStatus(ph['pair'][0], ph['pair'][1], 'down')
            active.append({'ports': ph['ports'], 'scenario': 'link'})
        elif ph['kind'] == 'node':
            for n in ph['nbrs']:
                net.configLinkStatus(ph['node'], n, 'down')
            active.append({'ports': ph['ports'], 'scenario': 'node'})
        elif ph['kind'] == 'flap':
            net.configLinkStatus(ph['pair'][0], ph['pair'][1], 'down')
            active.append({'ports': ph['ports'], 'scenario': 'flap', 'flap': True,
                           'pair': ph['pair'], 'next': time.time() + 1.5, 'up': False})
        elif ph['kind'] == 'congestion':
            h1, h2 = net.get('h1'), net.get('h2')
            h2.cmd('iperf -s -u -p %d >/dev/null 2>&1 &' % cong_flood_ports)
            time.sleep(0.3)
            h1.cmd('iperf -c %s -u -b 40M -t %d -p %d >/dev/null 2>&1 &'
                   % (mesh_spec.H2_IP, ph['dur'] + 1, cong_flood_ports))
            active.append({'ports': set(), 'scenario': 'congestion', 'cong': True})
        info('  [t=%5.1f] APPLY %-11s %s\n'
             % (time.time() - run0, ph['kind'], ph.get('pair') or ph.get('node') or ''))

    def revert_phase(ph):
        if ph['kind'] == 'link':
            net.configLinkStatus(ph['pair'][0], ph['pair'][1], 'up')
        elif ph['kind'] == 'node':
            for n in ph['nbrs']:
                net.configLinkStatus(ph['node'], n, 'up')
        elif ph['kind'] == 'flap':
            net.configLinkStatus(ph['pair'][0], ph['pair'][1], 'up')
        active[:] = [a for a in active if a['scenario'] != ph['kind']
                     or a.get('_ph') is not ph]

    # Attach a back-ref so revert removes the right interval.
    for ph in plan:
        ph['_apply_at'] = ph['t']
        ph['_revert_at'] = ph['t'] + ph['dur']

    run0 = time.time()
    # warmup poll to seed byte baselines (not recorded)
    _poll(net, spec, collector, None, None, topo, seed, run0)

    applied, reverted = set(), set()
    while True:
        now = time.time() - run0
        if now >= total:
            break
        for i, ph in enumerate(plan):
            if i not in applied and now >= ph['_apply_at']:
                apply_phase(ph)
                active[-1]['_ph'] = ph
                applied.add(i)
            if i in applied and i not in reverted and now >= ph['_revert_at']:
                revert_phase(ph)
                reverted.add(i)
        # flap toggling
        for a in active:
            if a.get('flap') and time.time() >= a['next']:
                a['up'] = not a['up']
                net.configLinkStatus(a['pair'][0], a['pair'][1],
                                     'up' if a['up'] else 'down')
                a['next'] = time.time() + 1.5
        _poll(net, spec, collector, active, writer, topo, seed, run0, stats)
        time.sleep(POLL)

    tg.stop()
    net.get('h1').cmd('pkill -f iperf 2>/dev/null')
    net.stop()


def _poll(net, spec, collector, active, writer, topo, seed, run0, stats=None):
    t_rel = round(time.time() - run0, 2)
    cong_on = active is not None and any(a.get('cong') for a in active)
    for s in spec.SWITCHES:
        dpid = spec.sw_to_dpid(s)
        collector.mark_request(dpid)
        out = net.get(s).cmd('ovs-ofctl -O OpenFlow13 dump-ports %s 2>/dev/null' % s)
        keys = collector.update_from_reply(dpid, parse_dump_ports(out))
        if writer is None:
            continue
        for (d, p) in keys:
            tel = collector.telemetry[(d, p)]
            scen, is_fault = 'normal', 0
            hit = next((a for a in (active or []) if (d, p) in a['ports']), None)
            if hit is not None:
                scen, is_fault = hit['scenario'], 1
            elif cong_on and tel['throughput_kbps'] > CONG_TH_KBPS:
                scen = 'congestion'
            n = collector.normalized((d, p))
            writer.writerow([topo, seed, t_rel, d, p,
                             tel['throughput_kbps'], tel['loss'], tel['delay_ms'],
                             round(n[0], 4), round(n[1], 4), round(n[2], 4),
                             scen, is_fault])
            if stats is not None:
                stats[scen] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    args = ap.parse_args()
    setLogLevel('warning')

    os.makedirs(os.path.dirname(DATASET), exist_ok=True)
    stats = Counter()
    with open(DATASET, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['topology', 'seed', 't_rel', 'dpid', 'port',
                    'throughput_kbps', 'loss', 'delay_ms',
                    'tput_n', 'loss_n', 'delay_n', 'scenario', 'is_fault'])
        specs = [(mesh_spec, 42), (mesh2_spec, 43)]
        for spec, seed in specs:
            print('=== collecting %s (seed %d) ===' % (spec.__name__, seed))
            collect_one(spec, seed, w, stats, args.quick)
            f.flush()

    total = sum(stats.values())
    print('\n=== DATASET %s ===' % DATASET)
    print('rows:', total)
    for k in ('normal', 'congestion', 'link', 'node', 'flap'):
        print('  %-11s %5d  (%.1f%%)' % (k, stats[k], 100.0 * stats[k] / max(total, 1)))
    print('  is_fault=1 :', stats['link'] + stats['node'] + stats['flap'])


if __name__ == '__main__':
    main()
