#!/usr/bin/env python3
"""
SHIELD closed-loop DECISION core (step S2.4) -- Ryu-free brain.

This holds the telemetry->detect->resolve->actuate decision logic *without* any
Ryu/OpenFlow plumbing, so the exact same brain drives two actuators:
  * framework/controller/shield_app.py -- production Ryu app (OFPFlowMod), and
  * experiments/exp_p2_recovery.py     -- single-process integration test that
    actuates via `ovs-ofctl` (reproducible in this sandbox, same pattern S1.7 used
    to validate detection without a live controller).

Model (matches controller/reroute_agent.py semantics, generalised to the mesh):
  * a PRIMARY path is installed proactively at low priority (the Resolver's best
    choice on the healthy graph);
  * on a link failure the Resolver re-selects the best surviving path and its flow
    plan is installed at HIGH priority, overriding the primary at the shared switches;
  * on link-up, if the primary is viable again the high-priority backup flows are
    deleted and the primary resumes (failback).

Everything is switch-name keyed; `flow_plan()` (S2.2) turns a chosen path into the
per-switch (dpid, eth_dst, out_port) entries each actuator installs.
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.telemetry.topology_graph import TopologyGraph
from framework.agents.resolver import Resolver, flow_plan
from baselines.base import path_is_viable
from topologies.mesh_spec import HOST_ATTACH

PRIO_PRIMARY = 10
PRIO_BACKUP = 100


class ShieldCore:
    def __init__(self, topo=None, src_host='h1', dst_host='h2', weights=None, k=3,
                 resolver=None):
        self.topo = topo or TopologyGraph()
        # A shared Resolver may be passed in (e.g. one the S3.2 Learner keeps updating
        # across an end-to-end run); otherwise build a fresh one from `weights`.
        self.resolver = resolver or Resolver(weights)
        self.src_host, self.dst_host = src_host, dst_host
        self.src_sw = HOST_ATTACH[src_host][0]
        self.dst_sw = HOST_ATTACH[dst_host][0]
        self.k = k

        best = self.resolver.select(self.topo.active_graph(),
                                    self.src_sw, self.dst_sw, k=k)
        if best is None:
            raise ValueError("no primary path %s->%s on healthy graph"
                             % (self.src_sw, self.dst_sw))
        self.primary = best['path']
        self.active = list(self.primary)
        self.on_backup = False
        self.backup_entries = []          # entries currently installed at PRIO_BACKUP

    # -- telemetry / graph updates ------------------------------------------
    def mark_down(self, a, b):
        """Record inter-switch link (a,b) as failed."""
        self.topo.down.add(frozenset({a, b}))

    def mark_up(self, a, b):
        self.topo.down.discard(frozenset({a, b}))

    # -- flow plans ----------------------------------------------------------
    def initial_plan(self):
        """Proactive PRIMARY flow entries (install at PRIO_PRIMARY)."""
        return flow_plan(self.topo.active_graph(), self.primary,
                         self.src_host, self.dst_host)

    def plan_recovery(self):
        """Re-resolve on the live graph and return the actuation delta, or None.

        Returns one of:
          {'action':'reroute',  'path', 'install':[entries], 'priority':PRIO_BACKUP}
          {'action':'failback', 'path', 'delete':[entries],  'priority':PRIO_BACKUP}
          None  (already optimal; nothing to change)
        """
        g = self.topo.active_graph()
        best = self.resolver.select(g, self.src_sw, self.dst_sw, k=self.k)
        if best is None:
            return None                    # fully partitioned -- nothing we can do
        path = best['path']

        # Back on primary and primary is usable again -> tear down the backup.
        if self.on_backup and path_is_viable(g, self.primary):
            deleted = self.backup_entries
            self.backup_entries = []
            self.on_backup = False
            self.active = list(self.primary)
            return {'action': 'failback', 'path': self.primary,
                    'delete': deleted, 'priority': PRIO_BACKUP}

        # Primary broken -> install (or switch to) the best surviving path.
        if path != self.primary:
            if path == self.active and self.on_backup:
                return None                # already rerouted here
            plan = flow_plan(g, path, self.src_host, self.dst_host)
            self.backup_entries = plan
            self.on_backup = True
            self.active = list(path)
            return {'action': 'reroute', 'path': path,
                    'install': plan, 'priority': PRIO_BACKUP}
        return None


if __name__ == '__main__':
    # Offline sanity: heal loop on the mesh (no Mininet).
    core = ShieldCore()
    print("primary:", '->'.join(core.primary))
    core.mark_down('s1', 's2')
    r = core.plan_recovery()
    print("after s1-s2 down:", r['action'], '->'.join(r['path']))
    core.mark_up('s1', 's2')
    r = core.plan_recovery()
    print("after s1-s2 up: ", r['action'], '->'.join(r['path']))
