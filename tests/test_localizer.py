#!/usr/bin/env python3
"""Unit test for FaultLocalizer (the production S1.6 component). venv python."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from framework.telemetry.topology_graph import TopologyGraph
from framework.telemetry.collector import TelemetryCollector
from framework.agents.localizer import FaultLocalizer


def test_localizes_exact_link_and_service_impact():
    tg, col = TopologyGraph(), TelemetryCollector()
    # s1 port2 <-> s2 port1 is an inter-switch link; mark it as having carried traffic
    col.telemetry[(1, 2)] = {'recent_max_kbps': 3000.0, 'drop_ratio': 1.0,
                             'throughput_kbps': 0.0, 'loss': 0.0, 'delay_ms': 30}
    loc = FaultLocalizer(tg, col)
    evt = loc.on_port_down(1, 2)
    assert evt is not None
    assert set(evt['failed_link']) == {'s1', 's2'}, evt['failed_link']
    assert set(map(tuple, evt['failed_ports'])) == {(1, 2), (2, 1)}, evt['failed_ports']
    assert evt['service_impacting'] is True
    # graph edge is now marked down
    assert frozenset({'s1', 's2'}) in tg.down


def test_host_port_returns_none():
    tg, col = TopologyGraph(), TelemetryCollector()
    loc = FaultLocalizer(tg, col)
    assert loc.on_port_down(1, 1) is None    # s1 port1 is host-facing


def test_idle_link_not_service_impacting():
    tg, col = TopologyGraph(), TelemetryCollector()
    loc = FaultLocalizer(tg, col)
    evt = loc.on_port_down(1, 4)             # s1-s5 link, no traffic recorded
    assert evt is not None
    assert set(evt['failed_link']) == {'s1', 's5'}
    assert evt['service_impacting'] is False


if __name__ == '__main__':
    test_localizes_exact_link_and_service_impact()
    test_host_port_returns_none()
    test_idle_link_not_service_impacting()
    print('test_localizer: ALL PASS')
