#!/usr/bin/env python3
"""
Generic Mininet builder: build any topology SPEC (mesh_spec / mesh2_spec / ...).

A "spec" is a module exposing SWITCHES, HOST_LINKS, SWITCH_LINKS, HOST_ATTACH and
sw_to_dpid(). Runs under sudo / system python.
"""
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink


def create_net(spec, stp=False):
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch,
                  link=TCLink, autoSetMacs=False, build=False)
    c0 = net.addController('c0', ip='127.0.0.1', port=6653)

    for h, (_sw, _port, mac, ip) in spec.HOST_ATTACH.items():
        net.addHost(h, ip=ip + '/24', mac=mac)
    for s in spec.SWITCHES:
        net.addSwitch(s, dpid='%016x' % spec.sw_to_dpid(s), protocols='OpenFlow13')
    for a, pa, b, pb, bw, delay in spec.HOST_LINKS + spec.SWITCH_LINKS:
        net.addLink(a, b, port1=pa, port2=pb, bw=bw, delay='%dms' % delay)

    net.build()
    c0.start()
    for s in spec.SWITCHES:
        net.get(s).start([c0])
    if stp:
        for s in spec.SWITCHES:
            sw = net.get(s)
            sw.cmd('ovs-vsctl set-fail-mode %s standalone' % s)
            sw.cmd('ovs-vsctl set bridge %s stp_enable=true' % s)

    # Static ARP between every host pair (no ARP flooding => loop-safe).
    hosts = list(spec.HOST_ATTACH)
    for h in hosts:
        hn = net.get(h)
        for other in hosts:
            if other != h:
                _sw, _p, omac, oip = spec.HOST_ATTACH[other]
                hn.cmd('arp -s %s %s' % (oip, omac))
    return net
