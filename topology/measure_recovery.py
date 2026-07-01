#!/usr/bin/env python3
"""
Quantitative recovery-time + REAL data-plane latency measurement (Paper fix #5).

Fixes two peer-review issues:
  1. The paper's "delay" state feature was the controller request->reply RTT
     (an SDN control-plane quantity), NOT end-to-end data-plane latency. Here we
     measure the REAL host-to-host RTT and jitter with ping on the data plane.
  2. The paper had no recovery-time number. We measure the traffic outage after a
     primary-link failure over N cycles:
        outage_ms = (packets_transmitted - packets_received) * interval_ms
     (only packets during the outage are lost, since the path is healthy before
      the cut and recovered after).

To make the latency REAL and non-trivial (a plain Mininet link has ~0 delay, so
"RTT" would be an artifact), every link carries an injected propagation delay via
TCLink. The primary and backup paths have DIFFERENT hop counts, so the reroute
produces a measurable change in data-plane RTT:

    h1 -1ms- s1 -5ms- s2 -5ms- s3 -1ms- h3     PRIMARY  (2 switch hops)
    h1 -1ms- s1 ------5ms------ s3 -1ms- h3     BACKUP   (1 switch hop, direct)

    expected RTT_primary ~= 2*(1+5+5+1) = 24 ms
    expected RTT_backup  ~= 2*(1+5+1)   = 14 ms

These are REAL data-plane measurements (real ping probes over emulated links with
known delays), not controller RTT. This is exactly the metric the paper should
report as "delay/latency".

Start the controller in the background FIRST, then run this (both need root):

  # event-driven controller:
  /home/mininet/venvs/ryu-4.34-py39/bin/ryu-manager controller/reroute_agent.py &
  sudo python3 topology/measure_recovery.py 5 2.0

  # PPO controller (recovery bounded by the 3 s telemetry poll):
  /home/mininet/venvs/ryu-4.34-py39/bin/ryu-manager controller/self_healing_agent.py &
  sudo python3 topology/measure_recovery.py 5 6.0

Args:  [n_cycles=5] [recover_wait_seconds=6.0] [fault_mode=link|node]

  fault_mode 'link' = single mid-path link s2-s3 down (fix #5 default).
  fault_mode 'node' = whole s2 fails (both s1-s2 and s2-s3 down). Use 'node' to
  compare against the OpenFlow fast-failover baseline (controller/failover_agent.py),
  which only recovers locally-observable faults. Head-to-head M1 comparison:
    ryu-manager controller/failover_agent.py & sudo python3 topology/measure_recovery.py 5 2.0 node
    ryu-manager controller/reroute_agent.py   & sudo python3 topology/measure_recovery.py 5 2.0 node
"""
import re
import sys
import time
import statistics

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel

H1_MAC = '00:00:00:00:00:01'
H3_MAC = '00:00:00:00:00:03'
H1_IP = '10.0.0.1'
H3_IP = '10.0.0.3'
INTERVAL = 0.02              # ping spacing -> recovery-time resolution (20 ms)
INTERVAL_MS = INTERVAL * 1000.0

# Injected one-way link delays (see module docstring for the resulting RTTs).
ACCESS_DELAY = '1ms'        # host<->edge switch
CORE_DELAY = '5ms'          # switch<->switch (primary hops and the backup hop)


def build():
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch, link=TCLink)
    c0 = net.addController('c0', ip='127.0.0.1', port=6653)
    h1 = net.addHost('h1', ip=H1_IP + '/24', mac=H1_MAC)
    h3 = net.addHost('h3', ip=H3_IP + '/24', mac=H3_MAC)
    s1 = net.addSwitch('s1', dpid='0000000000000001', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', dpid='0000000000000002', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', dpid='0000000000000003', protocols='OpenFlow13')
    net.addLink(h1, s1, port1=0, port2=1, delay=ACCESS_DELAY)
    net.addLink(h3, s3, port1=0, port2=1, delay=ACCESS_DELAY)
    net.addLink(s1, s2, port1=2, port2=1, delay=CORE_DELAY)   # primary hop 1
    net.addLink(s2, s3, port1=2, port2=2, delay=CORE_DELAY)   # primary hop 2
    net.addLink(s1, s3, port1=3, port2=3, delay=CORE_DELAY)   # backup (direct)
    net.build()
    c0.start()
    for s in (s1, s2, s3):
        s.start([c0])
    h1.cmd('arp -s %s %s' % (H3_IP, H3_MAC))
    h3.cmd('arp -s %s %s' % (H1_IP, H1_MAC))
    return net, h1, h3


def parse_latency(out):
    m = re.search(r'min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', out)
    return (float(m.group(2)), float(m.group(4))) if m else (None, None)


def parse_loss(out):
    m = re.search(r'(\d+) packets transmitted, (\d+) received', out)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def set_fault(net, mode, state):
    """Bring the primary path 'down'/'up'.

    'link' : single mid-path link s2-s3 (local only to s3).
    'node' : whole s2 fails -> both s1-s2 and s2-s3 (local to BOTH s1 and s3,
             so OpenFlow fast-failover can recover it; a plain single-link fault
             cannot be recovered by per-switch fast-failover -- see failover_agent.py)."""
    net.configLinkStatus('s2', 's3', state)
    if mode == 'node':
        net.configLinkStatus('s1', 's2', state)


def main():
    setLogLevel('warning')
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    recover_wait = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
    fault_mode = sys.argv[3] if len(sys.argv) > 3 else 'link'   # 'link' | 'node'
    if fault_mode not in ('link', 'node'):
        fault_mode = 'link'

    print("=== recovery measurement | %d cycles | recover_wait %.1fs | fault=%s | resolution %d ms ==="
          % (n, recover_wait, fault_mode, int(INTERVAL_MS)))
    net, h1, h3 = build()
    time.sleep(6)                                   # primary flows install
    h1.cmd('ping -i 0.05 %s > /dev/null 2>&1 &' % H3_IP)   # keep ports "active" (for PPO)
    time.sleep(8)                                   # >= 2 telemetry polls

    # --- (A) data-plane latency on the healthy PRIMARY path ---
    lat_p_avg, lat_p_jit = parse_latency(h1.cmd('ping -c 30 -i 0.05 %s' % H3_IP))

    # --- (B) data-plane latency on the BACKUP path (fail primary, let it recover) ---
    set_fault(net, fault_mode, 'down')
    time.sleep(recover_wait)                        # let controller install backup
    lat_b_avg, lat_b_jit = parse_latency(h1.cmd('ping -c 30 -i 0.05 %s' % H3_IP))
    set_fault(net, fault_mode, 'up')                # restore -> failback
    time.sleep(4.0)

    # --- (C) recovery-time (outage) over N fail/restore cycles ---
    outages = []
    count = int((1.0 + recover_wait) / INTERVAL)
    for c in range(n):
        mlog = '/tmp/meas_%d.log' % c
        h1.cmd('ping -c %d -i %s %s > %s 2>&1 &' % (count, INTERVAL, H3_IP, mlog))
        time.sleep(1.0)                             # healthy baseline
        set_fault(net, fault_mode, 'down')          # FAIL primary (stays down until read)
        time.sleep(recover_wait)                    # measure through recovery
        tx = rx = None                              # poll until ping wrote its summary
        for _ in range(50):
            tx, rx = parse_loss(open(mlog).read())
            if tx is not None:
                break
            time.sleep(0.2)
        outage = (tx - rx) * INTERVAL_MS if tx is not None else None
        outages.append(outage)
        set_fault(net, fault_mode, 'up')            # RESTORE -> controller failback
        print("cycle %d: outage %6.0f ms  (tx=%s rx=%s)" % (c, outage if outage is not None else -1, tx, rx))
        time.sleep(4.0)                             # let primary re-stabilize

    h1.cmd('pkill -f "ping -i 0.05" 2>/dev/null')
    net.stop()

    valid = [o for o in outages if o is not None]
    mean = statistics.mean(valid) if valid else float('nan')
    std = statistics.pstdev(valid) if len(valid) > 1 else 0.0
    print("\n==================== SUMMARY ====================")
    print("cycles measured        : %d" % len(valid))
    print("recovery time (ms)     : %.0f +/- %.0f  (resolution %d ms)" % (mean, std, int(INTERVAL_MS)))
    print("PRIMARY RTT (ms)       : avg %.3f   jitter(mdev) %.3f   [expected ~24]"
          % (lat_p_avg or -1, lat_p_jit or -1))
    print("BACKUP  RTT (ms)       : avg %.3f   jitter(mdev) %.3f   [expected ~14]"
          % (lat_b_avg or -1, lat_b_jit or -1))
    print("NOTE: RTT/jitter are REAL data-plane ping measurements over links with")
    print("      known injected delays (TCLink), NOT the controller request/reply")
    print("      RTT the paper previously mislabeled as 'delay'. The primary vs.")
    print("      backup difference reflects the extra switch hop through s2.")
    print("=================================================")


if __name__ == '__main__':
    main()
