#!/usr/bin/env python3
"""
S2.5 -- best-solution (Resolver) experiment on the big mesh.   PHASE-2 GATE.

Proves the headline claim "find the BEST recovery, not just A recovery." After the
primary path (A) fails, the two survivors B and C are given DIFFERENT live link
conditions (varying bw / loss), and we compare the post-recovery QoS obtained by
each recovery strategy:

    fixed_failover : pre-provisioned order -> always the first backup (B)
    shortest_path  : lowest-latency survivor -> B
    random_path    : a random survivor (seeded)
    Resolver (SHIELD): scores latency+capacity+loss+hops on OBSERVED telemetry
    oracle         : same objective with the TRUE link state (upper bound)

The strategies differ ONLY in the path they choose, so we measure the live QoS of
each surviving path once per seed (iperf TCP throughput + ping latency/loss) and map
each strategy -> its chosen path -> that measured QoS. This keeps the run honest and
cheap while every number is a real measurement.

Recovery-tuned weights (throughput/quality over raw latency) are fixed a priori and
used identically for SHIELD and the oracle; the control scenario (C degraded) checks
SHIELD is condition-aware (it must pick B there), not merely C-biased.

PASS: SHIELD beats fixed_failover AND shortest_path on composite QoS in >=3 of 4
scenarios, and lands within ~10% of the oracle.

Run:  sudo python3 -u experiments/exp_p2_resolver.py > results/logs/s2_5_resolver.log 2>&1
"""
import os
import re
import sys
import time
import statistics as st

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'topologies'))

from mininet.net import Mininet                                      # noqa: E402
from mininet.node import RemoteController, OVSKernelSwitch           # noqa: E402
from mininet.link import TCLink                                      # noqa: E402
from mininet.log import setLogLevel                                  # noqa: E402

from topologies import mesh_spec as SP                               # noqa: E402
from framework.pathing.k_paths import k_shortest_paths               # noqa: E402
from framework.agents.resolver import Resolver, flow_plan            # noqa: E402
from baselines.shortest_path import ShortestPath                     # noqa: E402
from baselines.fixed_failover import FixedFailover                   # noqa: E402
from baselines.random_path import RandomPath                         # noqa: E402
from baselines.oracle_path import OraclePath                         # noqa: E402

SEEDS = 5
IPERF_DUR = 5
# Recovery objective: prioritise goodput (capacity, low loss) over raw latency.
WEIGHTS = {'latency': 0.5, 'capacity': 1.0, 'loss': 1.5, 'hops': 0.1}

PATH_B = ['s1', 's3', 's4', 's6']
PATH_C = ['s1', 's5', 's7', 's6']
BOT_B = ('s1', 's3')          # bottleneck link we degrade for Path B
BOT_C = ('s1', 's5')          # bottleneck link we degrade for Path C

# (name, build-overrides {frozenset(link):{bw,loss}}, observed_bw{link:bw}, observed_loss{frozenset:frac})
SCENARIOS = [
    ('congest_B',      {frozenset(BOT_B): {'bw': 2}},              {BOT_B: 2}, {}),
    ('loss_B',         {frozenset(BOT_B): {'loss': 15}},           {},         {frozenset(BOT_B): 0.15}),
    ('congest_loss_B', {frozenset(BOT_B): {'bw': 3, 'loss': 10}},  {BOT_B: 3}, {frozenset(BOT_B): 0.10}),
    ('congest_C_ctrl', {frozenset(BOT_C): {'bw': 2, 'loss': 10}},  {BOT_C: 2}, {frozenset(BOT_C): 0.10}),
]


# -- topology with per-link overrides ----------------------------------------
def build_scenario(overrides):
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch,
                  link=TCLink, autoSetMacs=False, build=False)
    c0 = net.addController('c0', ip='127.0.0.1', port=6653)
    for h, (_sw, _p, mac, ip) in SP.HOST_ATTACH.items():
        net.addHost(h, ip=ip + '/24', mac=mac)
    for s in SP.SWITCHES:
        net.addSwitch(s, dpid='%016x' % SP.sw_to_dpid(s), protocols='OpenFlow13')
    for a, pa, b, pb, bw, delay in SP.HOST_LINKS + SP.SWITCH_LINKS:
        o = overrides.get(frozenset({a, b}), {})
        kw = dict(port1=pa, port2=pb, bw=o.get('bw', bw), delay='%dms' % delay)
        if 'loss' in o:
            kw['loss'] = o['loss']
        net.addLink(a, b, **kw)
    net.build()
    c0.start()
    for s in SP.SWITCHES:
        net.get(s).start([c0])
    for s in SP.SWITCHES:                       # fail-secure: only our flows forward
        net.get(s).cmd('ovs-vsctl set-fail-mode %s secure' % s)
    for h in SP.HOST_ATTACH:                     # static ARP (loop-safe)
        for other in SP.HOST_ATTACH:
            if other != h:
                _s, _p, omac, oip = SP.HOST_ATTACH[other]
                net.get(h).cmd('arp -s %s %s' % (oip, omac))
    return net


# -- actuation + measurement -------------------------------------------------
def install_only(net, path):
    g = SP.build_graph()
    for s in SP.SWITCHES:
        net.get(s).cmd('ovs-ofctl -O OpenFlow13 del-flows %s' % s)
    for e in flow_plan(g, path):
        sw = 's%d' % e['dpid']
        net.get(sw).cmd('ovs-ofctl -O OpenFlow13 add-flow %s '
                        '"priority=100,dl_dst=%s,actions=output:%d"'
                        % (sw, e['eth_dst'], e['out_port']))


def measure(net):
    h1, h2 = net.get('h1'), net.get('h2')
    h2.cmd('iperf -s -p 5010 >/dev/null 2>&1 &')
    time.sleep(0.5)
    out = h1.cmd('iperf -c %s -p 5010 -t %d' % (SP.H2_IP, IPERF_DUR))
    h2.cmd('pkill -f "iperf -s" 2>/dev/null')
    tm = re.findall(r'([\d.]+)\s+Mbits/sec', out)
    tput = float(tm[-1]) if tm else 0.0
    time.sleep(0.5)                              # let queues drain after iperf
    png = h1.cmd('ping -c 6 -i 0.3 -W 1 %s' % SP.H2_IP)
    lm = re.search(r'([\d.]+)% packet loss', png)     # may be a float, e.g. 33.3333%
    rm = re.search(r'= [\d.]+/([\d.]+)/', png)
    ploss = round(float(lm.group(1)), 1) if lm else 100.0
    lat = round(float(rm.group(1)), 1) if rm else 0.0
    return tput, lat, ploss


def composite(tput, lat, ploss):
    """Single QoS utility from MEASURED values (throughput-forward)."""
    return tput - 0.05 * lat - 0.2 * ploss


# -- strategy path choices (offline; live measurement done separately) -------
def topo_noA():
    g = SP.build_graph()
    del g['s1']['s2']
    del g['s2']['s1']
    return g


def observed(obs_bw):
    g = topo_noA()
    for (a, b), bw in obs_bw.items():
        g[a][b]['bw'] = bw
        g[b][a]['bw'] = bw
    return g


def tag(path):
    return 'B' if path == PATH_B else ('C' if path == PATH_C else '?')


def choices(obs_bw, obs_loss, seed):
    g = topo_noA()
    og = observed(obs_bw)
    return {
        'fixed_failover': tag(FixedFailover(SP.build_graph(), 's1', 's6').select(g, 's1', 's6')),
        'shortest_path':  tag(ShortestPath().select(g, 's1', 's6')),
        'random_path':    tag(RandomPath().select(g, 's1', 's6', seed=seed)),
        'SHIELD':         tag(Resolver(WEIGHTS).select(og, 's1', 's6', link_state=obs_loss)['path']),
        'oracle':         tag(OraclePath(WEIGHTS).select(og, 's1', 's6', link_state=obs_loss)),
    }


def main():
    setLogLevel('warning')
    os.system('mn -c >/dev/null 2>&1')
    methods = ['fixed_failover', 'shortest_path', 'random_path', 'SHIELD', 'oracle']
    results = {}      # scen -> method -> list of composite Q over seeds
    raw = {}          # scen -> path -> list of (tput,lat,ploss)

    for name, over, obs_bw, obs_loss in SCENARIOS:
        print('\n==== scenario %s ====' % name)
        net = build_scenario(over)
        try:
            # measure each surviving path once per seed under this scenario
            perpath = {'B': [], 'C': []}
            for pname, path in (('B', PATH_B), ('C', PATH_C)):
                install_only(net, path)
                time.sleep(1.0)
                for _ in range(SEEDS):
                    perpath[pname].append(measure(net))
                t = [m[0] for m in perpath[pname]]
                print('   path %s: tput=%.1f±%.1f Mb  lat=%.1fms  loss=%d%%'
                      % (pname, st.mean(t), (st.pstdev(t)), perpath[pname][0][1],
                         perpath[pname][0][2]))
            raw[name] = perpath
            # map each method -> chosen path -> that seed's measurement
            results[name] = {m: [] for m in methods}
            for seed in range(SEEDS):
                ch = choices(obs_bw, obs_loss, seed)
                for m in methods:
                    tp, la, pl = perpath[ch[m]][seed]
                    results[name][m].append(composite(tp, la, pl))
            picks = choices(obs_bw, obs_loss, 0)
            print('   picks: ' + '  '.join('%s=%s' % (m, picks[m]) for m in methods))
        finally:
            net.stop()
            os.system('mn -c >/dev/null 2>&1')

    # ---- verdict ----
    print('\n==== composite QoS (mean±std over %d seeds) ====' % SEEDS)
    wins = 0
    within10 = True
    for name, _o, _b, _l in SCENARIOS:
        r = results[name]
        line = []
        for m in methods:
            line.append('%s=%.1f±%.1f' % (m, st.mean(r[m]), st.pstdev(r[m])))
        print('%-16s ' % name + '  '.join(line))
        sh = st.mean(r['SHIELD'])
        beat_fixed = sh > st.mean(r['fixed_failover']) * 1.05
        beat_short = sh > st.mean(r['shortest_path']) * 1.05
        orc = st.mean(r['oracle'])
        near = abs(sh - orc) <= 0.10 * abs(orc) if orc else True
        within10 = within10 and near
        won = beat_fixed and beat_short
        wins += int(won)
        print('    -> SHIELD %.1f | beats fixed=%s beats shortest=%s | oracle=%.1f within10%%=%s%s'
              % (sh, beat_fixed, beat_short, orc, near,
                 '  [WIN]' if won else ('  [tie/control]')))

    ok = wins >= 3 and within10
    print('\n*** S2.5 GATE (SHIELD beats fixed AND shortest in >=3/4 scenarios, '
          'within ~10%% of oracle): %s  (wins=%d/4, within10=%s)'
          % ('PASS' if ok else 'FAIL', wins, within10))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
