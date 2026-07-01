#!/usr/bin/env python3
"""
Automated verification for sub-step 1.5: PPO-DECISION-triggered self-healing.

Unlike test_reroute.py (which uses the event-driven baseline controller), this
keeps real traffic flowing so Agent 1's telemetry marks the primary ports active,
then fails the primary link and confirms Agent 2 (PPO) decides to reroute and
connectivity is restored.

Run with controller/self_healing_agent.py already running:
    sudo python3 topology/test_reroute_ppo.py
"""
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel
import time
import re

H1_MAC = '00:00:00:00:00:01'
H3_MAC = '00:00:00:00:00:03'
H1_IP = '10.0.0.1'
H3_IP = '10.0.0.3'


def build():
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch)
    c0 = net.addController('c0', ip='127.0.0.1', port=6653)
    h1 = net.addHost('h1', ip=H1_IP + '/24', mac=H1_MAC)
    h3 = net.addHost('h3', ip=H3_IP + '/24', mac=H3_MAC)
    s1 = net.addSwitch('s1', dpid='0000000000000001', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', dpid='0000000000000002', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', dpid='0000000000000003', protocols='OpenFlow13')
    net.addLink(h1, s1, port1=0, port2=1)
    net.addLink(h3, s3, port1=0, port2=1)
    net.addLink(s1, s2, port1=2, port2=1)
    net.addLink(s2, s3, port1=2, port2=2)
    net.addLink(s1, s3, port1=3, port2=3)
    net.build()
    c0.start()
    for s in (s1, s2, s3):
        s.start([c0])
    h1.cmd('arp -s %s %s' % (H3_IP, H3_MAC))
    h3.cmd('arp -s %s %s' % (H1_IP, H1_MAC))
    return net, h1, h3


def ping_loss(h, count):
    out = h.cmd('ping -c %d -W 1 %s' % (count, H3_IP))
    m = re.search(r'(\d+)% packet loss', out)
    return int(m.group(1)) if m else 100


def main():
    setLogLevel('warning')
    net, h1, h3 = build()
    print(">>> waiting 6s for controller to install PRIMARY flows...")
    time.sleep(6)

    print(">>> [TEST 1] ping on PRIMARY path")
    loss1 = ping_loss(h1, 5)
    print("    packet loss = %d%%" % loss1)

    print(">>> starting continuous traffic so telemetry marks ports ACTIVE")
    h1.cmd('ping -i 0.2 %s > /dev/null 2>&1 &' % H3_IP)
    time.sleep(7)  # >= 2 poll cycles of healthy traffic (PPO should say 'do nothing')

    print(">>> [TEST 2] failing PRIMARY link (s2-s3 down); PPO must decide to reroute")
    net.configLinkStatus('s2', 's3', 'down')
    time.sleep(8)  # next poll: throughput->0 -> PPO action=1 -> reroute

    h1.cmd('pkill -f "ping -i 0.2" 2>/dev/null')
    time.sleep(1)

    print(">>> [TEST 3] fresh ping after PPO reroute (must be recovered)")
    loss2 = ping_loss(h1, 8)
    print("    packet loss = %d%%" % loss2)

    net.stop()

    print("\n==================== RESULT ====================")
    print("Primary-path loss     : %d%%" % loss1)
    print("Post-PPO-reroute loss : %d%%" % loss2)
    ok = loss1 < 50 and loss2 < 50
    print("VERDICT: %s" % ("PASS - PPO-triggered self-healing WORKS" if ok
                           else "FAIL - PPO did not recover connectivity"))
    print("(confirm the controller log shows '[AGENT2] PPO action=1' + '[ACTUATE]')")
    print("================================================")


if __name__ == '__main__':
    main()
