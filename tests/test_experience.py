#!/usr/bin/env python3
"""
Unit test for step S3.1 -- experience store + reward, offline (in-memory SQLite).

Checks:
  1. Reward math matches the documented formula exactly (incl. custom alpha/beta).
  2. Record round-trips: every field (incl. nested JSON) reads back identical, and
     the auto-computed reward equals compute_reward(outcome).
  3. Store queries: count, get, all-ordering, recent(n), by_fault, mean_reward.
  4. stringify_state turns frozenset link keys into JSON-safe 'a-b' keys.
  5. File-backed DB persists across reopen.

PASS: all asserts hold; `python3 tests/test_experience.py` -> "S3.1 PASS".
"""
import os
import sys
import tempfile

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.experience.reward import compute_reward, REWARD_ALPHA, REWARD_BETA
from framework.experience.store import ExperienceStore, stringify_state


def test_reward_math():
    o = {'throughput': 17.4, 'post_loss': 2.0, 'recovery_time': 0.25}
    expect = REWARD_BETA * 17.4 - REWARD_ALPHA * 2.0 - 0.25
    assert abs(compute_reward(o) - expect) < 1e-9, compute_reward(o)
    # custom weights + missing keys default to 0
    assert abs(compute_reward({'throughput': 10}, alpha=1.0, beta=2.0) - 20.0) < 1e-9
    assert compute_reward({}) == 0.0
    # a good high-throughput/low-loss recovery beats a bad one
    good = compute_reward({'throughput': 17.4, 'post_loss': 0, 'recovery_time': 0.2})
    bad = compute_reward({'throughput': 0.8, 'post_loss': 50, 'recovery_time': 0.2})
    assert good > bad
    print("  [1] reward formula + weights + ordering .......... OK")


def test_round_trip():
    s = ExperienceStore()
    state = stringify_state({frozenset({'s1', 's3'}): {'bw': 2, 'loss': 0.15}})
    cands = [['s1', 's3', 's4', 's6'], ['s1', 's5', 's7', 's6']]
    chosen = ['s1', 's5', 's7', 's6']
    outcome = {'throughput': 17.4, 'post_loss': 0.0, 'recovery_time': 0.25}
    rid = s.record('mesh', 's1-s2', state, cands, chosen, outcome, ts=123.0)
    r = s.get(rid)
    assert r['topology'] == 'mesh' and r['fault'] == 's1-s2' and r['ts'] == 123.0
    assert r['state'] == state, r['state']
    assert r['candidates'] == cands, r['candidates']
    assert r['chosen'] == chosen, r['chosen']
    assert r['outcome'] == outcome, r['outcome']
    assert abs(r['reward'] - compute_reward(outcome)) < 1e-9
    print("  [2] record round-trips (nested JSON) + reward .... OK")


def test_queries():
    s = ExperienceStore()
    s.record('mesh', 's1-s2', {}, [['s1', 's3', 's4', 's6']],
             ['s1', 's3', 's4', 's6'],
             {'throughput': 0.8, 'post_loss': 50.0, 'recovery_time': 0.25})
    s.record('mesh', 's1-s2', {}, [['s1', 's5', 's7', 's6']],
             ['s1', 's5', 's7', 's6'],
             {'throughput': 17.4, 'post_loss': 0.0, 'recovery_time': 0.25})
    s.record('mesh2', 's2-s4', {}, [['s2', 's4']], ['s2', 's4'],
             {'throughput': 9.0, 'post_loss': 1.0, 'recovery_time': 0.3})
    assert s.count() == 3
    assert [r['id'] for r in s.all()] == [1, 2, 3]
    assert [r['id'] for r in s.recent(2)] == [2, 3]      # chronological tail
    assert len(s.by_fault('s1-s2')) == 2
    assert len(s.by_fault('s2-s4')) == 1
    exp_mean = sum(compute_reward(r['outcome']) for r in s.all()) / 3
    assert abs(s.mean_reward() - exp_mean) < 1e-9
    print("  [3] count/get/all/recent/by_fault/mean_reward .... OK")


def test_persistence():
    path = os.path.join(tempfile.mkdtemp(), 'exp.db')
    s = ExperienceStore(path)
    s.record('mesh', 's1-s2', {}, [['s1', 's2', 's6']], ['s1', 's2', 's6'],
             {'throughput': 5.0, 'post_loss': 0.0, 'recovery_time': 0.1})
    s.close()
    s2 = ExperienceStore(path)          # reopen same file
    assert s2.count() == 1 and s2.get(1)['topology'] == 'mesh'
    s2.close()
    print("  [4] file-backed DB persists across reopen ........ OK")


if __name__ == '__main__':
    test_reward_math()
    test_round_trip()
    test_queries()
    test_persistence()
    print("S3.1 PASS")
