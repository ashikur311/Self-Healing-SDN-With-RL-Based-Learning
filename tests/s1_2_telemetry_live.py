#!/usr/bin/env python3
"""
S1.2 live test driver (run under sudo / system python; needs the TelemetryMonitor
Ryu app already running on :6653).

Brings up the mesh with STP, generates real TCP traffic h1->h2, reads the
telemetry snapshot the monitor writes WHILE traffic is flowing, and asserts the
PASS gate: telemetry is non-zero under traffic (and the monitor confirms the
graph matches the spec in its own log).
"""
import os
import sys
import time
import json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'topologies'))

from mininet.log import setLogLevel  # noqa: E402
import mesh  # noqa: E402

SNAP = os.path.join(ROOT, 'results', 'logs', 'telemetry_snapshot.json')


def main():
    setLogLevel('info')
    net = mesh.create_net(stp=True)
    print('*** STP converging (45s)...')
    net.get('s1').cmd('sleep 45')

    h1, h2 = net.get('h1'), net.get('h2')
    print('*** starting iperf traffic h1 -> h2')
    h2.cmd('iperf -s -p 5001 > /tmp/iperf_s.log 2>&1 &')
    time.sleep(1)
    h1.cmd('iperf -c 10.0.0.2 -p 5001 -t 40 > /tmp/iperf_c.log 2>&1 &')

    # Let the monitor poll several times WHILE traffic flows (poll = 3s).
    print('*** letting telemetry accumulate (~15s of live traffic)...')
    time.sleep(15)

    snap = {}
    if os.path.exists(SNAP):
        with open(SNAP) as f:
            snap = json.load(f)

    nonzero = {k: v for k, v in snap.items() if v.get('throughput_kbps', 0) > 1.0}
    print('\n*** telemetry ports with >1 kbps: %d / %d' % (len(nonzero), len(snap)))
    for k in sorted(nonzero, key=lambda k: -nonzero[k]['throughput_kbps'])[:8]:
        print('      %-6s tput=%9.2f kbps  loss=%.3f  delay=%6.2f ms'
              % (k, snap[k]['throughput_kbps'], snap[k]['loss'], snap[k]['delay_ms']))

    ok = len(snap) > 0 and len(nonzero) >= 2   # both endpoints of >=1 flow path
    print('\n*** S1.2 telemetry non-zero under traffic: %s' % ('PASS' if ok else 'FAIL'))
    net.stop()
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
