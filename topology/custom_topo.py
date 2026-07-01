from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel
import time

def create_topology():
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch)
    
    print("*** Adding controller")
    c0 = net.addController('c0', ip='127.0.0.1', port=6653)
    
    print("*** Adding hosts")
    h1 = net.addHost('h1', mac='00:00:00:00:00:01')
    h2 = net.addHost('h2', mac='00:00:00:00:00:02')
    h3 = net.addHost('h3', mac='00:00:00:00:00:03')
    
    print("*** Adding switches")
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')
    
    print("*** Creating links (Linear Topology)")
    net.addLink(h1, s1)
    net.addLink(h2, s2)
    net.addLink(h3, s3)
    net.addLink(s1, s2)
    net.addLink(s2, s3)
    
    print("*** Starting network")
    net.build()
    c0.start()
    s1.start([c0])
    s2.start([c0])
    s3.start([c0])
    
    print("*** Phase 1: LOW Congestion (Baseline)")
    net.pingAll()
    time.sleep(5)
    
    print("*** Phase 2: MEDIUM Congestion (Starting iperf servers)")
    h2.cmd('iperf -s -u -p 5001 &') 
    h3.cmd('iperf -s -u -p 5002 &') 
    h1.cmd('iperf -c %s -u -b 5M -t 10 -p 5001 &' % h2.IP())
    time.sleep(12)
    
    print("*** Phase 3: HIGH Congestion (Flooding the network)")
    h1.cmd('iperf -c %s -u -b 20M -t 15 -p 5001 &' % h2.IP())
    h1.cmd('iperf -c %s -u -b 20M -t 15 -p 5002 &' % h3.IP())
    time.sleep(15)
    
    print("*** Phase 4: FAULT INJECTION (Link Failure)")
    print("Taking down link s1-s2...")
    net.configLinkStatus('s1', 's2', 'down')
    time.sleep(15) 
    
    print("*** Phase 5: RECOVERY (Link Restored)")
    print("Bringing link s1-s2 back up...")
    net.configLinkStatus('s1', 's2', 'up')
    time.sleep(5)
    
    print("*** Traffic generation complete.")
    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    create_topology()