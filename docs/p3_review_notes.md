# Phase 3 (Learner) — Peer-review findings & revision backlog

Recorded 2026-07-01 after `/peer-reviewer` on S3.1–S3.4. Recommendation: **Major
revision** of the *evaluation/claims* (the learner mechanism is sound and honestly
motivated). None block Phase 4; clear before the framework paper (S4.3), ideally in
one batch with [[p2-review-debt]]. Ordered by leverage.

## Major (fix before paper)
- **M4 [highest leverage] — Missing adaptive baseline.** "online > frozen" only shows
  *some* adaptation beats *none* on a distribution built to punish non-adaptation. Add
  a principled ADAPTIVE non-learning baseline (sliding-window greedy / "repeat
  yesterday's best" / discounted-UCB) and show SHIELD-online matches or beats THAT.
  Owner: `ml-rl-researcher`.
- **M2 — H1 significance was tuned against the test.** Bring-up gave H1 p=0.125 (fail);
  changing WINDOW_DAYS 3->2, REPS 12->16, EPSILON 0.15->0.25 AND redefining "late" from
  days[6,7,8]->[7,8] produced p=0.031. Pre-register hyperparameters + regime split;
  report an H1 sensitivity grid (window in {2,3,4}, eps in {0.1,0.25}, late in {last2,
  last3}). Owner: `ml-rl-researcher`.
- **M3 — n=5 seeds -> floor p=1/32=0.031; no margin.** One seed flipping sign moves p
  to 0.19 (seed 3 only clears by +2.2). Bump to >=8-10 seeds. Owner: `ml-rl-researcher`.
- **M1 — Oversold as "objective-weight learning"; it's a 2-arm non-stationary bandit.**
  With only Paths B/C + static features, per-dim goodness is binary and collinear with
  path identity, so the learner really estimates sign(cov(is_C, reward)) and the choice
  collapses to "pick C iff w_cap>w_lat". Reframe as non-stationary bandit, OR add a
  >=3-path topology with non-collinear features so weight-learning is non-trivial.
  Owner: `ml-rl-researcher`/`network-engineer`.
- **M6 — Validation can't detect the forgetting it guards.** val_score = Pearson on
  held-out records from the SAME recent 2-day window (same regime as train), so it
  cannot catch forgetting of OLD regimes (the definition of catastrophic forgetting).
  Hold out a PRIOR regime as validation. Owner: `ml-rl-researcher`.

## Minor
- **M5 — reward's recovery_time term is inert** (constant 0.25 in exp_p3 `_outcome`),
  and both lossy arms ~0 throughput, so reward is essentially throughput. Vary
  recovery_time/loss meaningfully, or scope the claim to throughput-driven reward.
- **H2 "frozen degrades" is tautological** (frozen defined to pick C; schedule defined
  to move loss onto C) — present as an ablation baseline, not a finding.
- `import random` inside `Learner._split` -> hoist to module top.
- Day-0 online < frozen because e-greedy explores into the bad arm — note the early
  exploration tax.
- S3.4 QoS is trace-driven from LIVE anchors; fully-live D-day loop = future work
  (already in [[p2-review-debt]] + docs/s3_4_results.txt).

## What still stands (do NOT redo)
- The learner + forgetting guard work; the partial-observability motivation (loss
  unobservable -> learning necessary; with full telemetry the heuristic suffices) is a
  genuine, honest strength. QoS anchors are real measurements. The mechanism is sound;
  the fixes are about the EVALUATION being tuned/tautological/underpowered.
- Single highest-leverage first fix: **M4** (adaptive baseline) — turns "we built a
  learner" into "learning is the right tool here".
