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

Hardened (p2 review M2/M3): baselines now include the strong load-aware WIDEST_PATH
router, and SHIELD sees only NOISY loss while the oracle sees ground truth (so the
oracle is a genuine upper bound, not a copy of SHIELD). A scenario with loss on the
FAT path (loss_C_fat) separates SHIELD from widest_path.
PASS: SHIELD beats the loss-blind baselines (fixed AND shortest) in >=3 scenarios,
is never materially worse than widest_path and strictly beats it in >=1 scenario
(where loss decorrelates from bandwidth), and lands within ~10% of the oracle.

Run:  sudo python3 -u experiments/exp_p2_resolver.py > results/logs/s2_5_resolver.log 2>&1
"""
import os
import random
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
from baselines.widest_path import WidestPath                         # noqa: E402
from baselines.oracle_path import OraclePath                         # noqa: E402

SEEDS = 8                       # p2 review M4: match the learner's 8-seed rigor
IPERF_DUR = 5
# Recovery objective: prioritise goodput (capacity, low loss) over raw latency.
WEIGHTS = {'latency': 0.5, 'capacity': 1.0, 'loss': 1.5, 'hops': 0.1}
# p2 M3: SHIELD observes loss with noise; the oracle gets ground truth. Makes the
# oracle a genuine upper bound (not a copy of SHIELD).
SHIELD_LOSS_NOISE = 0.05

PATH_B = ['s1', 's3', 's4', 's6']
PATH_C = ['s1', 's5', 's7', 's6']
BOT_B = ('s1', 's3')          # bottleneck link we degrade for Path B
BOT_C = ('s1', 's5')          # bottleneck link we degrade for Path C

# (name, build-overrides {frozenset(link):{bw,loss}}, observed_bw{link:bw}, observed_loss{frozenset:frac})
# The last scenario (loss on the FAT path C, bandwidth nominal) is where the
# load-aware widest_path baseline fails and SHIELD's loss-awareness wins (p2 M2).
SCENARIOS = [
    ('congest_B',      {frozenset(BOT_B): {'bw': 2}},              {BOT_B: 2}, {}),
    ('loss_B',         {frozenset(BOT_B): {'loss': 15}},           {},         {frozenset(BOT_B): 0.15}),
    ('congest_loss_B', {frozenset(BOT_B): {'bw': 3, 'loss': 10}},  {BOT_B: 3}, {frozenset(BOT_B): 0.10}),
    ('congest_C_ctrl', {frozenset(BOT_C): {'bw': 2, 'loss': 10}},  {BOT_C: 2}, {frozenset(BOT_C): 0.10}),
    ('loss_C_fat',     {frozenset(BOT_C): {'loss': 20}},           {},         {frozenset(BOT_C): 0.20}),
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
    og = observed(obs_bw)                       # observed-bandwidth graph (load-aware)
    # SHIELD sees loss with per-seed noise; the oracle sees ground truth (p2 M3).
    rng = random.Random(('noise', seed))
    noisy_loss = {k: max(0.0, v + rng.gauss(0, SHIELD_LOSS_NOISE))
                  for k, v in obs_loss.items()}
    return {
        'fixed_failover': tag(FixedFailover(SP.build_graph(), 's1', 's6').select(g, 's1', 's6')),
        'shortest_path':  tag(ShortestPath().select(g, 's1', 's6')),
        'widest_path':    tag(WidestPath().select(og, 's1', 's6')),
        'random_path':    tag(RandomPath().select(g, 's1', 's6', seed=seed)),
        'SHIELD':         tag(Resolver(WEIGHTS).select(og, 's1', 's6', link_state=noisy_loss)['path']),
        'oracle':         tag(OraclePath(WEIGHTS).select(og, 's1', 's6', link_state=obs_loss)),
    }


def main():
    setLogLevel('warning')
    os.system('mn -c >/dev/null 2>&1')
    methods = ['fixed_failover', 'shortest_path', 'widest_path', 'random_path',
               'SHIELD', 'oracle']
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
    n_scen = len(SCENARIOS)
    print('\n==== composite QoS (mean±std over %d seeds) ====' % SEEDS)
    wins_blind = 0            # SHIELD beats the loss-blind baselines (fixed AND shortest)
    beats_widest = 0          # SHIELD strictly beats the load-aware widest_path
    ge_widest = True          # SHIELD never worse than widest_path (>= within noise)
    within10 = True           # SHIELD within ~10% of the (ground-truth) oracle
    for name, _o, _b, _l in SCENARIOS:
        r = results[name]
        line = []
        for m in methods:
            line.append('%s=%.1f±%.1f' % (m, st.mean(r[m]), st.pstdev(r[m])))
        print('%-16s ' % name + '  '.join(line))
        sh = st.mean(r['SHIELD'])
        wid = st.mean(r['widest_path'])
        orc = st.mean(r['oracle'])
        beat_blind = (sh > st.mean(r['fixed_failover']) * 1.05
                      and sh > st.mean(r['shortest_path']) * 1.05)
        beat_wid = sh > wid * 1.05
        ge_wid = sh >= wid - max(1.0, 0.05 * abs(wid))       # not materially worse
        near = abs(sh - orc) <= 0.10 * abs(orc) if orc else True
        wins_blind += int(beat_blind)
        beats_widest += int(beat_wid)
        ge_widest = ge_widest and ge_wid
        within10 = within10 and near
        print('    -> SHIELD %.1f | vs blind(fixed/shortest) beat=%s | vs widest %.1f '
              'beat=%s ge=%s | oracle=%.1f within10%%=%s'
              % (sh, beat_blind, wid, beat_wid, ge_wid, orc, near))

    # Hardened gate (p2 M2/M3): SHIELD must (a) beat the loss-blind baselines in most
    # scenarios, (b) never be materially worse than the strong load-aware widest_path
    # and strictly beat it where loss decorrelates from bandwidth, and (c) stay within
    # ~10% of a genuine (ground-truth) oracle while itself seeing only NOISY loss.
    ok = wins_blind >= 3 and ge_widest and beats_widest >= 1 and within10
    print('\n*** S2.5 GATE (hardened): %s' % ('PASS' if ok else 'FAIL'))
    print('    beats loss-blind (fixed&shortest): %d/%d (need >=3)' % (wins_blind, n_scen))
    print('    never worse than widest_path: %s ; strictly beats widest_path: %d/%d (need >=1)'
          % (ge_widest, beats_widest, n_scen))
    print('    within ~10%% of ground-truth oracle (SHIELD sees NOISY loss): %s' % within10)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
