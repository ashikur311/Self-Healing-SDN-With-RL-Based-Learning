# Phase 2 (Resolver) — Peer-review findings & revision backlog

Recorded 2026-07-01 after `/peer-reviewer` on S2.1–S2.5. Recommendation: **Major
revision** of the *experiment* (the code is sound; the S2.5 comparison is
constructed). None of these block Phase 3; they MUST be fixed before the framework
paper (S4.3). Ordered by leverage.

## Major (fix before paper)
- **M3 [RESOLVED 2026-07-01] — Oracle was a literal copy of SHIELD.**
  FIXED in the hardened S2.5 (exp_p2_resolver.py): SHIELD now sees loss with per-seed
  noise (sigma=0.05); the oracle sees ground truth -> genuinely independent. Result:
  SHIELD still matches the oracle (choices robust to that noise level; loss gaps
  0 vs 15-20% >> noise) -- honestly reported as robustness, not a copy. See
  docs/s2_5_results.txt.
- **M2 [RESOLVED 2026-07-01] — Missing load-aware baseline.**
  FIXED: added `baselines/widest_path.py` (max-bottleneck-bandwidth) + a differentiating
  scenario `loss_C_fat` (loss on the fat path). SHIELD ties widest_path on bandwidth
  problems (honest) and STRICTLY beats it on loss_C_fat (7.1 vs -11.7) where load-
  awareness alone fails. Gate re-passed (beats loss-blind 3/5, >= widest always,
  strictly beats widest 1/5, within 10% of oracle). See docs/s2_5_results.txt.
- **M1 — 3/4 win-rate is authored, not discovered.** Both blind baselines can only
  pick B, so "SHIELD beats both" == "B is degraded", set by the authors in 3 of 4
  scenarios. FIX: vary delay too; add C-degraded scenarios; report win-rate over a
  BALANCED scenario set with a stated selection protocol. Owner: `network-engineer`.
- **M4 — The S2.5 gate never runs the closed loop.** `exp_p2_resolver.py` selects
  paths OFFLINE (`choices()`) and measures paths SEPARATELY (`install_only`+`measure`);
  it does not invoke `shield_core`/`shield_app`/detection/actuation. S2.4 closed loop
  is a separate 0%-loss run. FIX: one end-to-end `shield_app` run under a
  varying-condition scenario, showing measured recovery QoS == offline prediction.
  Owner: `sdn-expert`.
- **M5 — Circular/tuned objective + ad-hoc composite.** SHIELD weights
  {lat0.5,cap1,loss1.5,hops0.1} prioritise the very quantities the metric
  `tput-0.05*lat-0.2*ploss` rewards, while baselines optimise latency. Coeffs
  unjustified, units mismatched (Mb/s-ms-%), loss double-counted (TCP already
  collapses under loss). FIX: normalise units, cite a QoS utility, add weight
  sensitivity analysis. Owner: `network-engineer`.
- **M6 — One topology, 2-way decision at the gate.** Every fault reduces to B-vs-C
  (k=3 unused). `mesh2_spec` exists (used in S1.7) but S2.5 runs only mesh. FIX:
  repeat the gate on mesh2. Owner: `network-engineer`.

## Minor
- **M7 — "5 seeds" overstates rigor:** selection is deterministic, so seeds capture
  only iperf/TCLink noise (repeated measurements, not independent trials). Reframe;
  add a significance statement or drop statistical language. Owner: `ml-rl-researcher`.
- Reported per-path `loss=50%` (congest_loss_B, 10% netem) is end-to-end ping loss
  under compounding + TCP collapse — label as such, not the injected value.
- `k_shortest_paths` import in exp_p2_resolver.py is unused; `raw` dict unused.
- Latencies ~73–157 ms for 15–30 ms paths are residual queueing post-iperf — document.

## What still stands (do NOT redo)
- The MECHANISM is correct: a load-aware resolver beats load-blind failover; S2.4
  shows a real end-to-end recovery (0% loss + failback). The intellectual honesty
  (SHIELD==oracle -> framework, not RL, is the contribution) is a genuine strength.
- Single highest-leverage first fix: **M3** (noisy-telemetry oracle) — turns a
  constructed result into a meaningful one and exposes the deployment failure mode.
