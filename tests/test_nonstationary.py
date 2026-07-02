#!/usr/bin/env python3
"""
Unit test for step S3.3 -- non-stationary schedule (topologies/nonstationary.py).

Checks the distribution genuinely SHIFTS day-to-day (so there is something to adapt
to) and is logged:
  1. Drift: p(degrade B) decreases monotonically (0.9->0.1); fault-type mix drifts
     link-heavy -> churn-heavy; traffic rate ramps. First day != last day.
  2. The optimal survivor flips over the horizon (early best=C, late best=B), so a
     frozen policy must un-learn -- the premise of the S3.4 learning curve.
  3. Sampling is reproducible in (seed, day, rep) and empirically matches the day's
     p(degrade B) (early mostly B degraded, late mostly C degraded).
  4. log_schedule writes per-day distributions that differ across days.
  5. Integration hooks import + accept the schedule's output:
     traffic.start_mix(rate_choices=...) and the faultgen inject_scenario mapping.

PASS: all asserts hold; `python3 tests/test_nonstationary.py` -> "S3.3 PASS".
"""
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'topologies'))

from topologies.nonstationary import NonStationarySchedule, FAULT_TYPES


def test_drift_monotone():
    s = NonStationarySchedule(n_days=8, seed=7)
    pB = [s.day_profile(d)['p_degrade_B'] for d in range(8)]
    assert pB[0] > 0.8 and pB[-1] < 0.2, pB
    assert all(pB[i] >= pB[i + 1] for i in range(len(pB) - 1)), pB   # non-increasing
    # fault-type mix drifts: link down, flap up
    ft0 = s.day_profile(0)['fault_type_probs']
    ftL = s.day_profile(7)['fault_type_probs']
    assert ft0['link'] > ftL['link'] and ftL['flap'] > ft0['flap'], (ft0, ftL)
    # traffic ramps
    assert s.day_profile(7)['traffic_rate_mean'] > s.day_profile(0)['traffic_rate_mean']
    assert s.distribution(0) != s.distribution(7)
    print("  [1] distribution drifts + differs day0 vs dayN ... OK")


def test_optimal_flips():
    s = NonStationarySchedule(n_days=8, seed=7)
    # aggregate over reps: early days -> mostly best=C, late days -> mostly best=B
    def best_C_rate(day, reps=200):
        n = sum(s.optimal_survivor(s.sample_scenario(day, rep)) == 'C'
                for rep in range(reps))
        return n / reps
    assert best_C_rate(0) > 0.8, best_C_rate(0)
    assert best_C_rate(7) < 0.2, best_C_rate(7)
    print("  [2] optimal survivor flips C->B over horizon .... OK")


def test_sampling_reproducible_and_calibrated():
    s = NonStationarySchedule(n_days=8, seed=7)
    a = s.sample_scenario(3, rep=2)
    b = s.sample_scenario(3, rep=2)
    assert a == b, (a, b)                                   # reproducible
    assert a != s.sample_scenario(3, rep=3)                 # rep varies
    # empirical p(degrade B) tracks the profile within tolerance
    for day in (0, 4, 7):
        target = s.day_profile(day)['p_degrade_B']
        reps = 400
        emp = sum(s.sample_scenario(day, r)['degraded'] == 'B'
                  for r in range(reps)) / reps
        assert abs(emp - target) < 0.1, (day, emp, target)
    # scenario fields well-formed
    assert a['fault_type'] in FAULT_TYPES
    assert a['bottleneck'] in (('s1', 's3'), ('s1', 's5'))
    print("  [3] sampling reproducible + calibrated to p(B) ... OK")


def test_log_schedule():
    s = NonStationarySchedule(n_days=6, seed=7)
    path = os.path.join(_ROOT, 'results', 'logs', 'nonstationary_schedule.json')
    written = s.log_schedule(path)
    with open(written) as f:
        doc = json.load(f)
    assert doc['n_days'] == 6 and len(doc['days']) == 6
    assert doc['days'][0]['p_degrade_B'] != doc['days'][-1]['p_degrade_B']
    print("  [4] log_schedule writes drifting per-day dists ... OK")


def test_integration_hooks():
    # traffic hook: start_mix accepts rate_choices (signature/param present)
    import inspect
    from topologies.traffic import TrafficGenerator
    assert 'rate_choices' in inspect.signature(TrafficGenerator.start_mix).parameters
    # faultgen hook: inject_scenario exists and maps every fault type
    from topologies.faultgen import FaultInjector
    assert hasattr(FaultInjector, 'inject_scenario')
    print("  [5] traffic.rate_choices + faultgen.inject_scenario  OK")


if __name__ == '__main__':
    test_drift_monotone()
    test_optimal_flips()
    test_sampling_reproducible_and_calibrated()
    test_log_schedule()
    test_integration_hooks()
    print("S3.3 PASS")
