#!/usr/bin/env python3
"""
SHIELD learner (step S3.2) -- update the Resolver from experience, day by day.

Every N recoveries (a "day") this re-fits the Resolver's objective weights from the
ExperienceStore, so SHIELD's path selection improves as it sees more of the world.
It holds out a VALIDATION slice (split BEFORE fitting) and only accepts an update
that does not hurt held-out performance -- the guard against catastrophic forgetting.

What is learned
---------------
The Resolver scores a candidate by  w . goodness(path)  where goodness is the
per-dimension min-max score (latency/capacity/loss/hops, higher = better) within
the candidate set. The learner estimates, per dimension, how strongly a path's
goodness in that dimension has historically CO-VARIED with realized reward, and
sets the weight to that covariance (clipped at 0, then scaled to mean 1). Intuition:
"up-weight the path-features that have actually predicted good recoveries." With
enough varied experience these converge toward the hand-tuned recovery weights --
i.e. the framework DISCOVERS the objective instead of us hand-setting it.

Feedback is bandit (we only observe the outcome of the path we installed), so this
is an off-policy, correlational estimate -- honest about its limits; exploration is
introduced in S3.3's non-stationary schedule.

Records consumed (from ExperienceStore.all()): each must carry candidate QoS, i.e.
  record['candidates'] = [{'path':[...], 'latency':, 'capacity':, 'loss':, 'hops':}, ...]
  record['chosen']     = [...]         (one of the candidate paths)
  record['reward']     = float
SHIELD produces this directly from Resolver.select()['ranked'] (which carries qos).

Stdlib only (no numpy) -- runs under the system interpreter like the store.
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from framework.agents.resolver import Resolver, DEFAULT_WEIGHTS, _normalize

FEATURES = ['latency', 'capacity', 'loss', 'hops']
HIGHER_BETTER = {'latency': False, 'capacity': True, 'loss': False, 'hops': False}
FORGET_EPS = 1e-6          # tolerance: accept only if val score doesn't drop below this


# -- small stats (stdlib) ----------------------------------------------------
def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _cov(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n - 1)


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


# -- feature extraction ------------------------------------------------------
def chosen_goodness(record):
    """Goodness vector [latency,capacity,loss,hops] of the CHOSEN path, normalized
    within that record's candidate set (same orientation the Resolver uses)."""
    cands = record['candidates']
    if not cands or not isinstance(cands[0], dict) or 'latency' not in cands[0]:
        raise ValueError("records need candidate QoS "
                         "(path+latency/capacity/loss/hops); got %r" % (cands[:1],))
    good = {}
    for f in FEATURES:
        good[f] = _normalize([c[f] for c in cands], HIGHER_BETTER[f])
    idx = next((i for i, c in enumerate(cands) if c['path'] == record['chosen']), None)
    if idx is None:
        raise ValueError("chosen path %r not among candidates" % (record['chosen'],))
    return [good[f][idx] for f in FEATURES]


def _dataset(records):
    X = [chosen_goodness(r) for r in records]
    R = [float(r['reward']) for r in records]
    return X, R


def _score(weights_vec, x):
    return sum(w * xi for w, xi in zip(weights_vec, x))


def val_score(weights, records):
    """Held-out fit quality: Pearson corr between w.goodness(chosen) and reward.
    Higher = the weights rank chosen-path features consistently with real reward."""
    if len(records) < 2:
        return 0.0
    wv = [weights[f] for f in FEATURES]
    X, R = _dataset(records)
    return _pearson([_score(wv, x) for x in X], R)


# -- learner -----------------------------------------------------------------
class Learner:
    def __init__(self, resolver=None, val_fraction=0.3):
        self.resolver = resolver or Resolver()
        self.val_fraction = val_fraction
        self.updates = 0

    def fit_weights(self, records):
        """Covariance-based weight estimate from training records, or None if the
        experience carries no usable signal (all covariances <= 0)."""
        if len(records) < 2:
            return None
        X, R = _dataset(records)
        cov = []
        for d in range(len(FEATURES)):
            cov.append(max(0.0, _cov([x[d] for x in X], R)))
        total = sum(cov)
        if total <= 0:
            return None
        scale = len(FEATURES) / total            # scale to mean weight 1.0
        return {f: cov[d] * scale for d, f in enumerate(FEATURES)}

    def _split(self, records, seed):
        """Deterministic train/val split, made BEFORE fitting (integrity rule)."""
        import random
        idx = list(range(len(records)))
        random.Random(seed).shuffle(idx)
        n_val = max(1, int(round(len(records) * self.val_fraction)))
        val_i = set(idx[:n_val])
        train = [records[i] for i in idx if i not in val_i]
        val = [records[i] for i in val_i]
        return train, val

    def update(self, records, seed=0):
        """Re-fit weights from `records`; accept only if held-out score does not
        drop (forgetting guard). Returns a report dict; mutates the Resolver's
        weights in place iff accepted."""
        old = dict(self.resolver.weights)
        report = {'train_n': 0, 'val_n': 0, 'old_weights': old,
                  'new_weights': old, 'val_before': 0.0, 'val_after': 0.0,
                  'accepted': False, 'reason': ''}
        if len(records) < 4:
            report['reason'] = 'too few records'
            return report

        train, val = self._split(records, seed)
        report['train_n'], report['val_n'] = len(train), len(val)
        new = self.fit_weights(train)
        if new is None:
            report['reason'] = 'no signal in experience'
            return report

        vb = val_score(old, val)
        va = val_score(new, val)
        report.update(new_weights=new, val_before=vb, val_after=va)
        if va >= vb - FORGET_EPS:
            self.resolver.weights.update(new)
            self.updates += 1
            report['accepted'] = True
            report['reason'] = 'val did not drop'
        else:
            report['reason'] = 'rejected: val dropped (forgetting guard)'
        return report

    def update_from_store(self, store, seed=0):
        return self.update(store.all(), seed=seed)


if __name__ == '__main__':
    # Offline demo: reward driven by capacity+low-loss -> learner up-weights them.
    import random
    rng = random.Random(1)
    recs = []
    for _ in range(40):
        cap1, cap2 = rng.uniform(2, 20), rng.uniform(2, 20)
        ls1, ls2 = rng.uniform(0, 0.3), rng.uniform(0, 0.3)
        cands = [{'path': ['s1', 'B', 's6'], 'latency': 15, 'capacity': cap1, 'loss': ls1, 'hops': 3},
                 {'path': ['s1', 'C', 's6'], 'latency': 30, 'capacity': cap2, 'loss': ls2, 'hops': 3}]
        pick = rng.randint(0, 1)                  # exploratory experience (cf. S3.3)
        reward = cands[pick]['capacity'] - 10 * cands[pick]['loss'] + rng.gauss(0, 0.5)
        recs.append({'candidates': cands, 'chosen': cands[pick]['path'], 'reward': reward})
    lr = Learner()
    print("before:", {k: round(v, 2) for k, v in lr.resolver.weights.items()})
    rep = lr.update(recs)
    print("after :", {k: round(v, 2) for k, v in lr.resolver.weights.items()})
    print("val_before=%.3f val_after=%.3f accepted=%s (%s)"
          % (rep['val_before'], rep['val_after'], rep['accepted'], rep['reason']))
