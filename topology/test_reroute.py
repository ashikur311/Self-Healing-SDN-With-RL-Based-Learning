#!/usr/bin/env python3
"""
Automated verification for Paper fix #1 (self-healing reroute), no interactive CLI.

Builds the redundant topology, checks h1<->h3 connectivity on the primary path,
tears down the primary link (s2-s3), and re-checks connectivity to confirm the
controller auto-installed the backup path (s1-s3).

Run (controller must already be running):
    sudo python3 topology/test_reroute.py
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
    s1.start([c0])
    s2.start([c0])
    s3.start([c0])
    h1.cmd('arp -s %s %s' % (H3_IP, H3_MAC))
    h3.cmd('arp -s %s %s' % (H1_IP, H1_MAC))
    return net, h1, h3


def ping_loss(h1, count):
    out = h1.cmd('ping -c %d -W 1 %s' % (count, H3_IP))
    m = re.search(r'(\d+)% packet loss', out)
    return int(m.group(1)) if m else 100


def main():
    setLogLevel('warning')
    net, h1, h3 = build()
    print(">>> waiting 5s for controller to install PRIMARY flows...")
    time.sleep(5)

    print(">>> [TEST 1] ping h1->h3 on PRIMARY path")
    loss1 = ping_loss(h1, 5)
    print("    packet loss = %d%%" % loss1)

    print(">>> [TEST 2] breaking PRIMARY link (s2-s3 down)")
    net.configLinkStatus('s2', 's3', 'down')
    time.sleep(3)  # let OFPPortStatus fire + backup flows install

    print(">>> [TEST 3] ping h1->h3 after fault (must recover via s1-s3 backup)")
    loss2 = ping_loss(h1, 8)
    print("    packet loss = %d%%" % loss2)

    net.stop()

    print("\n==================== RESULT ====================")
    print("Primary-path loss : %d%%" % loss1)
    print("Post-fault  loss  : %d%%" % loss2)
    ok = loss1 < 50 and loss2 < 50
    print("VERDICT: %s" % ("PASS - self-healing reroute WORKS" if ok
                           else "FAIL - reroute did not recover connectivity"))
    print("================================================")


if __name__ == '__main__':
    main()
