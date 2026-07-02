#!/usr/bin/env python3
"""
Unit test for step S3.2 -- learner loop (framework/agents/learner.py), offline.

Checks:
  1. From exploratory experience where reward is driven by capacity+low-loss, an
     update is ACCEPTED, the Resolver's weights CHANGE, capacity is up-weighted over
     the (noise) latency/hops dims, and the held-out validation score does not drop.
  2. chosen_goodness extraction + val_score are correct/bounded; the split is made
     before fitting (train_n + val_n == len(records)).
  3. Forgetting/no-op guards: no-signal experience (constant features) and too-few
     records both leave the Resolver's weights UNCHANGED (accepted=False).
  4. End-to-end via ExperienceStore: record exploratory recoveries, learn from the
     store, weights update.

PASS: all asserts hold; `python3 tests/test_learner.py` -> "S3.2 PASS".
"""
import os
import random
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.agents.resolver import Resolver, DEFAULT_WEIGHTS
from framework.agents.learner import (Learner, chosen_goodness, val_score,
                                      FEATURES)
from framework.experience.store import ExperienceStore


def _explore_records(n=40, seed=1, cap_driven=True):
    """Records with RANDOM (exploratory) path choice; reward driven by the chosen
    path's capacity minus loss (so capacity/loss carry signal, latency/hops don't)."""
    rng = random.Random(seed)
    recs = []
    for _ in range(n):
        c1, c2 = rng.uniform(2, 20), rng.uniform(2, 20)
        l1, l2 = rng.uniform(0, 0.3), rng.uniform(0, 0.3)
        cands = [{'path': ['s1', 'B', 's6'], 'latency': 15, 'capacity': c1, 'loss': l1, 'hops': 3},
                 {'path': ['s1', 'C', 's6'], 'latency': 30, 'capacity': c2, 'loss': l2, 'hops': 3}]
        pick = rng.randint(0, 1)
        if cap_driven:
            reward = cands[pick]['capacity'] - 10 * cands[pick]['loss'] + rng.gauss(0, 0.5)
        else:
            reward = rng.gauss(0, 1.0)                 # no signal
        recs.append({'candidates': cands, 'chosen': cands[pick]['path'], 'reward': reward})
    return recs


def test_accepted_update_learns_capacity():
    recs = _explore_records()
    lr = Learner(Resolver())
    before = dict(lr.resolver.weights)
    rep = lr.update(recs, seed=0)
    after = lr.resolver.weights
    assert rep['accepted'], rep
    assert after != before, after
    # capacity drives reward -> its weight should dominate the noise dims
    assert after['capacity'] > after['latency'], after
    assert after['capacity'] > after['hops'], after
    assert rep['val_after'] >= rep['val_before'] - 1e-6, rep
    assert -1.0 <= rep['val_after'] <= 1.0
    print("  [1] accepted update up-weights capacity, val ok ... OK")


def test_feature_and_split():
    recs = _explore_records(n=20, seed=2)
    x = chosen_goodness(recs[0])
    assert len(x) == len(FEATURES) and all(0.0 <= v <= 1.0 for v in x), x
    v = val_score(DEFAULT_WEIGHTS, recs)
    assert -1.0 <= v <= 1.0, v
    lr = Learner(Resolver(), val_fraction=0.3)
    rep = lr.update(recs, seed=0)
    assert rep['train_n'] + rep['val_n'] == len(recs)
    assert rep['val_n'] == max(1, round(len(recs) * 0.3))
    print("  [2] goodness/val bounded; split before fit ....... OK")


def test_guards_leave_weights_unchanged():
    # no signal (random reward) -> no accepted update
    lr = Learner(Resolver())
    before = dict(lr.resolver.weights)
    rep = lr.update(_explore_records(n=40, seed=3, cap_driven=False), seed=0)
    assert not rep['accepted'], rep
    assert lr.resolver.weights == before, lr.resolver.weights
    # constant features -> zero covariance -> 'no signal in experience'
    flat = [{'candidates': [{'path': ['a'], 'latency': 1, 'capacity': 1, 'loss': 0, 'hops': 1}],
             'chosen': ['a'], 'reward': r} for r in (1, 2, 3, 4, 5, 6)]
    lr2 = Learner(Resolver())
    rep2 = lr2.update(flat, seed=0)
    assert not rep2['accepted']
    # too few records
    lr3 = Learner(Resolver())
    rep3 = lr3.update(_explore_records(n=3), seed=0)
    assert not rep3['accepted'] and rep3['reason'] == 'too few records'
    print("  [3] guards (no-signal / flat / too-few) no-op ..... OK")


def test_store_integration():
    store = ExperienceStore()
    for r in _explore_records(n=40, seed=5):
        store.record('mesh', 's1-s2', state={}, candidates=r['candidates'],
                     chosen=r['chosen'], outcome={}, reward=r['reward'])
    lr = Learner(Resolver())
    before = dict(lr.resolver.weights)
    rep = lr.update_from_store(store, seed=0)
    assert rep['accepted'] and lr.resolver.weights != before, rep
    print("  [4] end-to-end learn from ExperienceStore ......... OK")


if __name__ == '__main__':
    test_accepted_update_learns_capacity()
    test_feature_and_split()
    test_guards_leave_weights_unchanged()
    test_store_integration()
    print("S3.2 PASS")
