#!/usr/bin/env python3
"""
SHIELD fault injector (step S1.3).

Injects SCHEDULED, reproducible faults into a running mesh and logs each one with
a timestamp + ground-truth label, so later steps can (a) train/evaluate the
detector against known truth and (b) show that CONGESTION (a soft, link-still-up
degradation) is not the same as a FAULT (a hard link/node failure).

Fault types
-----------
  link_down / link_up : single inter-switch link down / restore
  multi_link          : several links down at once
  switch_down / _up   : node fault, emulated by isolating a switch (all its
                        inter-switch links down) -- reversible, and neighbours
                        still see port-down events
  congestion          : UDP flood between hosts; link stays UP but loss/latency
                        rise -> the "not a fault" negative case
  flap                : N down/up cycles on a link

Schedules are YAML in configs/ (JSON is also accepted -- valid YAML). Each event:
    {t: <sec from start>, type: <above>, target: <...>, label: <ground truth>, ...}

Runs under sudo / system python (drives Mininet), like mesh.py.

    sudo python3 topologies/faultgen.py --schedule configs/faults_demo.yaml
    sudo python3 topologies/faultgen.py --test
"""
import os
import sys
import time
import json
import argparse

import yaml

from mininet.log import setLogLevel, info

sys.path.insert(0, os.path.dirname(__file__))          # for mesh / mesh_spec
import mesh                                             # noqa: E402
from mesh_spec import build_graph, HOST_ATTACH         # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
LOG_DIR = os.path.join(ROOT, 'results', 'logs')


class FaultLog:
    """Ground-truth timeline of everything the injector did."""

    def __init__(self, name, t0):
        self.name = name
        self.t0 = t0
        self.entries = []

    def record(self, ftype, target, label=None, **extra):
        e = {'t_rel': round(time.time() - self.t0, 2),
             'wall': time.strftime('%H:%M:%S'),
             'type': ftype, 'target': target, 'label': label}
        e.update(extra)
        self.entries.append(e)
        info('[FAULT t=%6.2fs] %-12s %-16s %s\n'
             % (e['t_rel'], ftype, str(target), label or ''))
        return e

    def types(self):
        return {e['type'] for e in self.entries}

    def save(self, path=None):
        path = path or os.path.join(LOG_DIR, 'faults_%s.json' % self.name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump({'name': self.name, 'events': self.entries}, f, indent=2)
        info('*** fault timeline saved: %s\n' % path)
        return path


class FaultInjector:
    def __init__(self, net, name='run'):
        self.net = net
        self.graph = build_graph()
        self.log = FaultLog(name, time.time())
        self._isolated = {}   # switch -> [neighbours] currently downed for a node fault

    # -- link primitives -----------------------------------------------------
    def _find_link(self, a, b):
        na, nb = self.net.get(a), self.net.get(b)
        links = self.net.linksBetween(na, nb)
        return links[0] if links else None

    def _set_link(self, a, b, up):
        self.net.configLinkStatus(a, b, 'up' if up else 'down')

    def link_down(self, target, label='fault:link'):
        a, b = target
        self._set_link(a, b, False)
        return self.log.record('link_down', [a, b], label)

    def link_up(self, target, label=None):
        a, b = target
        self._set_link(a, b, True)
        return self.log.record('link_up', [a, b], label)

    def multi_link(self, target, label='fault:multi_link'):
        for a, b in target:
            self._set_link(a, b, False)
        return self.log.record('multi_link', target, label)

    # -- node fault (isolate a switch) --------------------------------------
    def switch_down(self, target, label='fault:node'):
        s = target
        nbrs = list(self.graph.get(s, {}))
        for n in nbrs:
            self._set_link(s, n, False)
        self._isolated[s] = nbrs
        return self.log.record('switch_down', s, label, isolated_from=nbrs)

    def switch_up(self, target, label=None):
        s = target
        for n in self._isolated.pop(s, list(self.graph.get(s, {}))):
            self._set_link(s, n, True)
        return self.log.record('switch_up', s, label)

    # -- congestion (soft: NOT a fault) -------------------------------------
    def congestion(self, target, rate='30M', dur=10, label='congestion'):
        src, dst = target
        dst_ip = HOST_ATTACH[dst][3]
        hs, hd = self.net.get(src), self.net.get(dst)
        hd.cmd('iperf -s -u -p 5002 > /tmp/cong_s.log 2>&1 &')
        time.sleep(0.5)
        hs.cmd('iperf -c %s -u -b %s -t %d -p 5002 > /tmp/cong_c.log 2>&1 &'
               % (dst_ip, rate, dur))
        return self.log.record('congestion', [src, dst], label, rate=rate, dur=dur)

    # -- flapping ------------------------------------------------------------
    def flap(self, target, cycles=3, period=2, label='fault:flap'):
        a, b = target
        for _ in range(cycles):
            self._set_link(a, b, False)
            time.sleep(period / 2.0)
            self._set_link(a, b, True)
            time.sleep(period / 2.0)
        return self.log.record('flap', [a, b], label, cycles=cycles, period=period)

    # -- scheduler -----------------------------------------------------------
    _DISPATCH = {'link_down': 'link_down', 'link_up': 'link_up',
                 'multi_link': 'multi_link', 'switch_down': 'switch_down',
                 'switch_up': 'switch_up', 'congestion': 'congestion',
                 'flap': 'flap'}

    def run_schedule(self, events):
        start = time.time()
        for ev in sorted(events, key=lambda e: e['t']):
            wait = ev['t'] - (time.time() - start)
            if wait > 0:
                time.sleep(wait)
            method = getattr(self, self._DISPATCH[ev['type']])
            kwargs = {k: v for k, v in ev.items()
                      if k not in ('t', 'type', 'target')}
            method(ev.get('target'), **kwargs)

    def inject_scenario(self, scenario):
        """Non-stationary S3.3: fail the primary path per a day's sampled scenario.

        The scenario (from topologies.nonstationary.sample_scenario) carries a
        drifting FAULT TYPE -- so the fault distribution SHIFTS across days:
          'link' -> down the primary link,
          'node' -> isolate the primary's mid switch (kills Path A too),
          'flap' -> flap the primary link a few cycles then leave it down.
        The degraded-survivor condition itself is applied at build time
        (scenario['bottleneck']/bw/loss); this only injects the primary failure."""
        a, b = scenario['primary_fault']
        ftype = scenario.get('fault_type', 'link')
        lbl = 'day%s:%s' % (scenario.get('day', '?'), ftype)
        if ftype == 'node':
            return self.switch_down(b, label=lbl)          # isolate s2 -> Path A dead
        if ftype == 'flap':
            self.flap([a, b], cycles=scenario.get('cycles', 2),
                      period=scenario.get('period', 1), label=lbl)
            return self.link_down([a, b], label=lbl)        # leave it down after flapping
        return self.link_down([a, b], label=lbl)


def load_schedule(path):
    with open(path) as f:
        return yaml.safe_load(f)


DEMO_SCHEDULE = {
    'name': 'demo',
    'seed': 42,
    'events': [
        {'t': 2,  'type': 'link_down',  'target': ['s1', 's2'], 'label': 'fault:link:s1-s2'},
        {'t': 5,  'type': 'link_up',    'target': ['s1', 's2']},
        {'t': 7,  'type': 'multi_link', 'target': [['s3', 's4'], ['s5', 's7']],
         'label': 'fault:multi:B+C'},
        {'t': 10, 'type': 'switch_down', 'target': 's4', 'label': 'fault:node:s4'},
        {'t': 13, 'type': 'switch_up',  'target': 's4'},
        {'t': 15, 'type': 'congestion', 'target': ['h1', 'h2'], 'rate': '30M',
         'dur': 6, 'label': 'congestion:h1-h2'},
        {'t': 18, 'type': 'flap',       'target': ['s1', 's3'], 'cycles': 3,
         'period': 2, 'label': 'fault:flap:s1-s3'},
    ],
}

EXPECTED_TYPES = {'link_down', 'link_up', 'multi_link', 'switch_down',
                  'switch_up', 'congestion', 'flap'}


def self_test():
    """S1.3: every fault type is injected + logged with a timestamp."""
    net = mesh.create_net(stp=True)
    info('*** STP converging (45s) so congestion has a path to flood...\n')
    net.get('s1').cmd('sleep 45')

    inj = FaultInjector(net, name='selftest')

    # Spot-check that a link_down actually changes interface state.
    link = inj._find_link('s1', 's2')
    inj.link_down(['s1', 's2'], label='fault:link:s1-s2')
    down_ok = (link is not None and not link.intf1.isUp() and not link.intf2.isUp())
    inj.link_up(['s1', 's2'])
    up_ok = link.intf1.isUp() and link.intf2.isUp()

    # Run the rest of the demo (skip the two link ops we already did).
    rest = [e for e in DEMO_SCHEDULE['events']
            if not (e['type'] in ('link_down', 'link_up')
                    and e['target'] == ['s1', 's2'])]
    inj.run_schedule(rest)
    inj.log.save()

    got = inj.log.types()
    missing = EXPECTED_TYPES - got
    info('\n*** fault types injected: %s\n' % sorted(got))
    info('*** link_down took interface DOWN: %s ; link_up restored: %s\n'
         % (down_ok, up_ok))
    ok = not missing and down_ok and up_ok
    if missing:
        info('!!! FAIL: missing fault types %s\n' % sorted(missing))
    info('\n*** S1.3 SELF-TEST: %s\n' % ('PASS' if ok else 'FAIL'))
    net.stop()
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--schedule')
    ap.add_argument('--test', action='store_true')
    args = ap.parse_args()
    setLogLevel('info')

    if args.test:
        sys.exit(0 if self_test() else 1)

    sched = load_schedule(args.schedule) if args.schedule else DEMO_SCHEDULE
    net = mesh.create_net(stp=True)
    info('*** STP converging (45s)...\n')
    net.get('s1').cmd('sleep 45')
    inj = FaultInjector(net, name=sched.get('name', 'run'))
    inj.run_schedule(sched['events'])
    inj.log.save()
    net.stop()


if __name__ == '__main__':
    main()
