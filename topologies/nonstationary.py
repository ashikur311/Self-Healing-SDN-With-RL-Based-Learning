#!/usr/bin/env python3
"""
Non-stationary day schedule (step S3.3) -- give SHIELD something to ADAPT to.

The learner (S3.2) only matters if the world DRIFTS: if the best recovery path were
fixed, a frozen model would suffice. This planner makes the fault + traffic
distribution shift across "days" so the optimal recovery genuinely changes over
time, and so the online learner gets EXPLORATORY, drifting experience (which the
bandit learner needs).

Two things drift across D days (t = day/(D-1), 0..1):
  1. WHICH survivor is degraded after the primary fails -- early days degrade Path B
     (so fat Path C is best), late days degrade Path C (so Path B is best). A model
     frozen on "prefer C" must therefore un-learn and re-learn.  p_degrade_B: 0.9->0.1
  2. The FAULT-TYPE mix -- early days are calm single-link faults, late days add node
     isolation and flapping (more churn):  link 0.8->0.3, node 0.1->0.3, flap 0.1->0.4
  3. Traffic INTENSITY ramps up (rate choices grow), so congestion pressure rises.

This module is Mininet-free and deterministic (seeded), so it is unit-tested offline
and shared by the live S3.4 experiment. faultgen.FaultInjector.inject_scenario() and
traffic.TrafficGenerator.start_mix(rate_choices=...) consume its per-day output.
"""
import json
import os

PRIMARY_FAULT = ('s1', 's2')          # the primary path (A) link we fail each day
BOT_B = ('s1', 's3')                  # bottleneck link of survivor Path B
BOT_C = ('s1', 's5')                  # bottleneck link of survivor Path C
FAULT_TYPES = ('link', 'node', 'flap')


def _lerp(a, b, t):
    return a + (b - a) * t


def _sample_categorical(rng, labels, probs):
    r = rng.random() * sum(probs)
    acc = 0.0
    for lab, p in zip(labels, probs):
        acc += p
        if r <= acc:
            return lab
    return labels[-1]


class NonStationarySchedule:
    def __init__(self, n_days=8, seed=7):
        self.n_days = n_days
        self.seed = seed

    # -- the drifting distribution (deterministic in `day`) ------------------
    def day_profile(self, day):
        t = day / (self.n_days - 1) if self.n_days > 1 else 0.0
        p_deg_b = round(_lerp(0.9, 0.1, t), 4)
        ft = {'link': round(_lerp(0.8, 0.3, t), 4),
              'node': round(_lerp(0.1, 0.3, t), 4),
              'flap': round(_lerp(0.1, 0.4, t), 4)}
        rate_hi = int(round(_lerp(4, 10, t)))        # top burst rate grows with load
        if t < 0.34:
            regime = 'B-degraded (Path C best), calm'
        elif t < 0.67:
            regime = 'mixed'
        else:
            regime = 'C-degraded (Path B best), churny'
        return {'day': day, 't': round(t, 4), 'regime': regime,
                'p_degrade_B': p_deg_b, 'p_degrade_C': round(1 - p_deg_b, 4),
                'fault_type_probs': ft,
                'traffic_rate_mean': round(_lerp(3, 8, t), 2),
                'rate_choices': list(range(2, rate_hi + 1, 2)) or [2],
                'n_bursts': int(round(_lerp(4, 8, t)))}

    def distribution(self, day):
        """The comparable per-day distribution vector (for logging / drift checks)."""
        p = self.day_profile(day)
        return {'p_degrade_B': p['p_degrade_B'],
                'fault_type_probs': p['fault_type_probs'],
                'traffic_rate_mean': p['traffic_rate_mean']}

    # -- concrete draws ------------------------------------------------------
    def _rng(self, day, rep=0):
        import random
        return random.Random((self.seed, day, rep))

    def sample_scenario(self, day, rep=0):
        """One concrete day scenario (reproducible in (seed, day, rep))."""
        prof = self.day_profile(day)
        rng = self._rng(day, rep)
        degraded = 'B' if rng.random() < prof['p_degrade_B'] else 'C'
        bottleneck = BOT_B if degraded == 'B' else BOT_C
        ftp = prof['fault_type_probs']
        fault_type = _sample_categorical(rng, FAULT_TYPES,
                                         [ftp['link'], ftp['node'], ftp['flap']])
        return {'day': day, 'rep': rep, 'regime': prof['regime'],
                'primary_fault': PRIMARY_FAULT, 'fault_type': fault_type,
                'degraded': degraded, 'bottleneck': bottleneck,
                'bw': rng.choice([2, 3]), 'loss': rng.choice([10, 15, 20]),
                'rate_choices': prof['rate_choices'], 'n_bursts': prof['n_bursts']}

    @staticmethod
    def optimal_survivor(scenario):
        """Ground-truth best recovery path given which survivor is degraded."""
        return 'C' if scenario['degraded'] == 'B' else 'B'

    def build_overrides(self, scenario):
        """TCLink build overrides (as in S2.5) for the degraded survivor's bottleneck."""
        return {frozenset(scenario['bottleneck']):
                {'bw': scenario['bw'], 'loss': scenario['loss']}}

    # -- logging (the S3.3 deliverable: distribution differs day-to-day) -----
    def log_schedule(self, path=None):
        path = path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'results', 'logs', 'nonstationary_schedule.json')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        doc = {'n_days': self.n_days, 'seed': self.seed,
               'days': [self.day_profile(d) for d in range(self.n_days)]}
        with open(path, 'w') as f:
            json.dump(doc, f, indent=2)
        return path


if __name__ == '__main__':
    sched = NonStationarySchedule(n_days=8, seed=7)
    print("day  p(deg B)  regime                              rates      sample->degraded/best")
    for d in range(sched.n_days):
        p = sched.day_profile(d)
        sc = sched.sample_scenario(d)
        print("%2d   %.2f      %-34s %-10s deg=%s best=%s"
              % (d, p['p_degrade_B'], p['regime'], p['rate_choices'],
                 sc['degraded'], sched.optimal_survivor(sc)))
    print("logged ->", sched.log_schedule())
