#!/usr/bin/env python3
"""
Redundant SDN topology for the self-healing reroute demo (Paper fix #1).

Unlike the original LINEAR topology (h1-s1-s2-s3-h3), this one adds a direct
BACKUP link s1<->s3 so that traffic can actually be REROUTED when the primary
path through s2 fails.

    h1 --- s1 =============(primary)============ s2 =========== s3 --- h3
            \                                                   /
             \-------------(backup: s1-s3 direct)-------------/

Loop-safety: this creates a physical loop (s1-s2-s3-s1). To avoid broadcast
storms WITHOUT a spanning-tree protocol, forwarding is done entirely by explicit
proactive flows installed by controller/reroute_agent.py, and host ARP is made
STATIC here so no ARP flooding is ever needed. That is why we do NOT use a
learning/flooding switch for this experiment.

Explicit port numbering (the controller relies on these):
    s1: port1 -> h1     port2 -> s2      port3 -> s3 (BACKUP)
    s2: port1 -> s1     port2 -> s3
    s3: port1 -> h3     port2 -> s2      port3 -> s1 (BACKUP)

Run (needs sudo):  sudo python3 topology/reroute_topo.py
"""
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel


H1_MAC = '00:00:00:00:00:01'
H3_MAC = '00:00:00:00:00:03'
H1_IP = '10.0.0.1'
H3_IP = '10.0.0.3'


def create_topology():
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch)

    print("*** Adding controller")
    c0 = net.addController('c0', ip='127.0.0.1', port=6653)

    print("*** Adding hosts")
    h1 = net.addHost('h1', ip=H1_IP + '/24', mac=H1_MAC)
    h3 = net.addHost('h3', ip=H3_IP + '/24', mac=H3_MAC)

    print("*** Adding switches")
    s1 = net.addSwitch('s1', dpid='0000000000000001', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', dpid='0000000000000002', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', dpid='0000000000000003', protocols='OpenFlow13')

    print("*** Creating links with FIXED port numbers")
    # Host links
    net.addLink(h1, s1, port1=0, port2=1)   # h1-eth0 <-> s1-eth1
    net.addLink(h3, s3, port1=0, port2=1)   # h3-eth0 <-> s3-eth1
    # Primary path (through s2)
    net.addLink(s1, s2, port1=2, port2=1)   # s1-eth2 <-> s2-eth1
    net.addLink(s2, s3, port1=2, port2=2)   # s2-eth2 <-> s3-eth2
    # Backup path (direct s1<->s3)
    net.addLink(s1, s3, port1=3, port2=3)   # s1-eth3 <-> s3-eth3

    print("*** Starting network")
    net.build()
    c0.start()
    s1.start([c0])
    s2.start([c0])
    s3.start([c0])

    print("*** Installing STATIC ARP (removes need for ARP flooding => loop-safe)")
    h1.cmd('arp -s %s %s' % (H3_IP, H3_MAC))
    h3.cmd('arp -s %s %s' % (H1_IP, H1_MAC))

    print("""
*** Ready. Suggested test:
    mininet> h1 ping h3            # works via PRIMARY path (through s2)
    mininet> link s2 s3 down       # break the primary path
    ... ping should auto-recover via the s1-s3 BACKUP path ...
    mininet> link s2 s3 up         # restore
""")
    CLI(net)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    create_topology()
