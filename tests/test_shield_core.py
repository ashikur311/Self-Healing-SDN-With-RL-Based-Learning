#!/usr/bin/env python3
"""
Unit test for step S2.4 -- closed-loop DECISION core (shield_core.py), offline.

Validates the brain the Ryu app and the live experiment share, without Mininet:
  1. Primary is the Resolver's best on the healthy graph; initial plan is valid.
  2. Link failure -> reroute to the best surviving path (flows to install).
  3. Link-up  -> failback to primary (backup flows to delete).
  4. Node failure that kills the primary -> reroute avoids the node.
  5. Idempotency: re-planning without a change returns None.

PASS: all asserts hold; `python3 tests/test_shield_core.py` -> "S2.4-core PASS".
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.controller.shield_core import ShieldCore, PRIO_BACKUP
from baselines.base import path_is_viable


def test_primary_and_initial_plan():
    core = ShieldCore()
    assert core.primary == ['s1', 's2', 's6'], core.primary
    plan = core.initial_plan()
    dpids = {e['dpid'] for e in plan}
    assert dpids == {1, 2, 6}, dpids            # switches on Path A
    assert {e['dir'] for e in plan} == {'fwd', 'rev'}
    print("  [1] primary=A, initial plan covers s1,s2,s6 ...... OK")


def test_reroute_then_failback():
    core = ShieldCore()
    core.mark_down('s1', 's2')
    r = core.plan_recovery()
    assert r['action'] == 'reroute' and r['path'] == ['s1', 's3', 's4', 's6'], r
    assert 's2' not in r['path'] and r['priority'] == PRIO_BACKUP
    assert all(path_is_viable(core.topo.active_graph(), r['path']) for _ in [0])
    assert core.on_backup is True
    # heal -> failback deletes exactly the backup entries
    core.mark_up('s1', 's2')
    f = core.plan_recovery()
    assert f['action'] == 'failback' and f['path'] == ['s1', 's2', 's6'], f
    assert f['delete'] == r['install']
    assert core.on_backup is False
    print("  [2] reroute A->B on fault, failback B->A on heal .. OK")


def test_node_failure():
    core = ShieldCore()
    # isolate s2 (both of Path A's links) -> primary dead
    core.mark_down('s1', 's2')
    core.mark_down('s2', 's6')
    r = core.plan_recovery()
    assert r['action'] == 'reroute' and 's2' not in r['path'], r
    print("  [3] node isolation -> reroute avoids the node ..... OK")


def test_idempotent():
    core = ShieldCore()
    assert core.plan_recovery() is None          # healthy, on primary already
    core.mark_down('s1', 's2')
    assert core.plan_recovery()['action'] == 'reroute'
    assert core.plan_recovery() is None          # already rerouted -> no-op
    print("  [4] no-op when nothing to change .................. OK")


if __name__ == '__main__':
    test_primary_and_initial_plan()
    test_reroute_then_failback()
    test_node_failure()
    test_idempotent()
    print("S2.4-core PASS")
