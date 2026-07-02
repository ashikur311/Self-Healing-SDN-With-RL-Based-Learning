#!/usr/bin/env python3
"""
SHIELD live traffic generator (step S1.4).

Generates a realistic, non-blocking mix of flows between the hosts so that:
  * telemetry under NORMAL operation is non-trivial (varying throughput/loss),
  * CONGESTION (offered load > path capacity) looks different from a hard FAULT
    but is NOT trivially separable -- some UDP bursts exceed Path A's 5 Mbps and
    cause real loss while every link stays UP.

All flows are launched as background processes (iperf servers + staggered clients
with in-shell `sleep` offsets), so the caller can inject faults concurrently
(S1.5). Runs under sudo / system python, like mesh.py / faultgen.py.

    sudo python3 topologies/traffic.py --test
    sudo python3 topologies/traffic.py --profile configs/traffic_default.yaml
"""
import os
import sys
import time
import random
import argparse

import yaml

from mininet.log import setLogLevel, info

sys.path.insert(0, os.path.dirname(__file__))
import mesh                                     # noqa: E402
from mesh_spec import HOST_ATTACH              # noqa: E402


def _ip(host):
    return HOST_ATTACH[host][3]


class TrafficGenerator:
    def __init__(self, net, seed=42):
        self.net = net
        self.rng = random.Random(seed)
        self._servers = set()          # (host, port, udp) already listening
        self._flows = []               # description of every launched flow

    def _ensure_server(self, host, port, udp):
        key = (host, port, udp)
        if key in self._servers:
            return
        h = self.net.get(host)
        u = '-u' if udp else ''
        h.cmd('iperf -s %s -p %d > /tmp/iperf_s_%d.log 2>&1 &' % (u, port, port))
        self._servers.add(key)

    def flow(self, src, dst, port, udp=False, rate=None, dur=10, start=0):
        """Launch one background flow src->dst (client self-delays `start` sec)."""
        self._ensure_server(dst, port, udp)
        h = self.net.get(src)
        opts = '-u -b %s' % rate if udp else ''
        if not udp and rate:
            opts = '-b %s' % rate    # TCP rate hint (iperf accepts on some builds)
        cmd = 'iperf -c %s -p %d %s -t %d' % (_ip(dst), port, opts, dur)
        h.cmd('(sleep %d; %s) > /tmp/iperf_c_%s_%d.log 2>&1 &' % (start, cmd, src, port))
        self._flows.append({'src': src, 'dst': dst, 'port': port,
                            'proto': 'udp' if udp else 'tcp',
                            'rate': rate, 'dur': dur, 'start': start})

    def start_mix(self, dur=30, n_bursts=6, rate_choices=None):
        """Persistent background flows + randomized foreground bursts over `dur`.

        `rate_choices` (list of Mbit ints) lets the non-stationary schedule (S3.3)
        shift traffic intensity per "day"; defaults to the stationary [2,4,6,8]."""
        rate_choices = rate_choices or [2, 4, 6, 8]
        # -- background (persistent, both directions) --
        self.flow('h1', 'h2', port=5101, udp=False, dur=dur, start=0)          # TCP fwd
        self.flow('h2', 'h1', port=5102, udp=True, rate='1M', dur=dur, start=0)  # UDP rev
        # -- foreground bursts (varying rate/proto/offset) --
        port = 5110
        for _ in range(n_bursts):
            start = self.rng.randint(1, max(1, dur - 8))
            blen = self.rng.randint(3, 6)
            udp = self.rng.random() < 0.6
            src, dst = ('h1', 'h2') if self.rng.random() < 0.5 else ('h2', 'h1')
            rate = '%dM' % self.rng.choice(rate_choices)   # some exceed Path-A 5M
            self.flow(src, dst, port=port, udp=udp, rate=rate if udp else None,
                      dur=blen, start=start)
            port += 1
        info('*** launched %d flows (2 background + %d bursts)\n'
             % (len(self._flows), n_bursts))
        return self._flows

    def iface_bytes(self, host, iface=None):
        iface = iface or '%s-eth0' % host
        h = self.net.get(host)
        rx = h.cmd('cat /sys/class/net/%s/statistics/rx_bytes' % iface).strip()
        tx = h.cmd('cat /sys/class/net/%s/statistics/tx_bytes' % iface).strip()
        return int(rx or 0), int(tx or 0)

    def stop(self):
        for h in ('h1', 'h2'):
            self.net.get(h).cmd('pkill -f iperf 2>/dev/null')


DEFAULT_PROFILE = {'name': 'default', 'seed': 42, 'dur': 30, 'n_bursts': 6}


def self_test():
    """S1.4: iperf flows run; telemetry/interfaces show realistic (varied) load."""
    net = mesh.create_net(stp=True)
    info('*** STP converging (45s)...\n')
    net.get('s1').cmd('sleep 45')

    tg = TrafficGenerator(net, seed=42)
    r0 = tg.iface_bytes('h1')
    d0 = tg.iface_bytes('h2')
    flows = tg.start_mix(dur=25, n_bursts=6)
    info('*** running traffic for ~28s...\n')
    net.get('h1').cmd('sleep 28')
    r1 = tg.iface_bytes('h1')
    d1 = tg.iface_bytes('h2')
    tg.stop()

    h1_tx = r1[1] - r0[1]
    h2_rx = d1[0] - d0[0]
    protos = {f['proto'] for f in flows}
    rates = {f['rate'] for f in flows if f['rate']}
    info('\n*** flows launched: %d  protocols: %s  distinct UDP rates: %s\n'
         % (len(flows), sorted(protos), sorted(rates)))
    info('*** h1 tx delta: %.1f MB   h2 rx delta: %.1f MB\n'
         % (h1_tx / 1e6, h2_rx / 1e6))

    ok = (len(flows) >= 6 and {'tcp', 'udp'} <= protos
          and len(rates) >= 2 and h1_tx > 2e6 and h2_rx > 2e6)
    info('\n*** S1.4 SELF-TEST: %s\n' % ('PASS' if ok else 'FAIL'))
    net.stop()
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile')
    ap.add_argument('--test', action='store_true')
    args = ap.parse_args()
    setLogLevel('info')
    if args.test:
        sys.exit(0 if self_test() else 1)

    prof = DEFAULT_PROFILE
    if args.profile:
        with open(args.profile) as f:
            prof = yaml.safe_load(f)
    net = mesh.create_net(stp=True)
    info('*** STP converging (45s)...\n')
    net.get('s1').cmd('sleep 45')
    tg = TrafficGenerator(net, seed=prof.get('seed', 42))
    tg.start_mix(dur=prof.get('dur', 30), n_bursts=prof.get('n_bursts', 6))
    net.get('h1').cmd('sleep %d' % (prof.get('dur', 30) + 3))
    tg.stop()
    net.stop()


if __name__ == '__main__':
    main()
