# PROJECT WORKING STEPS — AI Self-Healing SDN Framework (SHIELD)

> **Execution checklist.** Do the steps **in order, top to bottom**. Each step has:
> **Build → Test → PASS gate → Output.** Do not start a step until the one before it
> PASSES. This is the *how-to-do-it*; `FRAMEWORK_PLAN.md` is the *architecture/why*;
> `PAPER_ROADMAP.md` is the already-finished paper.
>
> **Goal (one line):** big topology → live packets + fault injection → detect the
> problem → find the BEST recovery → update itself and learn over time.

---

## How to work EACH step (repeat this loop every time)
1. Write the **test + PASS number first** (in the step's result doc).
2. Build the component **small and in isolation**; add a unit test.
3. Integrate; run the **live testbed** (topology + traffic + fault).
4. Run ≥5 seeds; save raw output to `results/logs/`.
5. Fill the PASS gate. **PASS** → write `docs/<step>_results.txt` + tick the tracker.
   **FAIL** → write down why, fix or descope, do **not** proceed.
6. Commit the working step before starting the next.

**Integrity rules (always):** split before balancing · mean±std over ≥5 seeds ·
every number traces to a script · if a simple baseline wins, say so · never fake a
result. Run `/peer-reviewer` after each PHASE.

---

# PHASE 0 — Foundation (setup & scaffolding)   [~1 session]

**Reuse, don't rewrite:** `reroute_agent.py` (actuation), `measure_recovery.py`
(harness), `evaluate_agent.py` (eval protocol) already work — wrap them.

- [x] **S0.1 — Environment + disk check.** DONE 2026-07-01: disk grown to 20G (13G free); all tools present (Ryu 4.34, OVS 2.13.8, Mininet sys-py3.8, SB3 2.7.1 venv-py3.9). See `docs/env.txt`.
  - Build: confirm tools; **free disk (VM is 100% full)**.
  - Test: `df -h /` shows free space; `ryu-manager --version`, `ovs-vsctl --version`,
    `python -c "import stable_baselines3, mininet"` all succeed.
  - PASS: ≥1 GB free; all imports OK.
  - Output: `docs/env.txt` (pinned versions: Ryu 4.34 / OVS 2.13 / OF1.3 / SB3 / Py).

- [ ] **S0.2 — Create the package skeleton.**
  - Build: make dirs `framework/{agents,controller,telemetry,pathing,experience}`,
    `topologies/`, `experiments/`, `baselines/`, `configs/`, `results/{logs,figures,tables}`,
    `tests/`. Add empty `__init__.py`.
  - Test: `python -c "import framework"` works.
  - Output: the directory tree from `FRAMEWORK_PLAN.md` §2.

- [ ] **S0.3 — Migrate working code (wrap).**
  - Build: `framework/controller/actuation.py` = the `_add_flow`/`trigger_reroute`/
    `restore_primary` logic from `reroute_agent.py`; `experiments/harness.py` = the
    measurement logic from `measure_recovery.py` (recovery time + RTT/jitter).
  - Test: import both; run the **existing triangle recovery** through the new modules.
  - PASS: triangle still recovers (regression) — same ~40 ms as before.
  - Output: green regression run in `results/logs/regression_triangle.txt`.

- [ ] **S0.4 — Config + run harness.**
  - Build: `configs/` YAML (topology, traffic, fault schedule, seeds); a `run.sh`
    that starts a controller (background) + a topology + an experiment.
  - Test: `bash run.sh triangle smoke` brings it up and tears down cleanly.
  - Output: reproducible one-command runner.

---

# PHASE 1 — M-A: big topology + live traffic + fault injection + DETECT   [~2–3 sessions]
*Proves: "big topology → live packet → fault injection → it detects the problem."*

- [x] **S1.1 — Big topology.** DONE 2026-07-01: `topologies/mesh.py` — 7 OVS switches,
    3 node-disjoint paths (A/B/C, deliberately different bw/delay), TCLinks, fixed
    MACs + static ARP. Self-test: pingall 0% drop, 3 disjoint paths confirmed.
    See `results/logs/s1_1_mesh_selftest.txt`, `results/figures/mesh_topology.dot`.
  - Build: `topologies/mesh.py` — 6–8 OVS switches, **≥3 disjoint paths** between the
    test hosts, `TCLink` delays/bandwidth, fixed MACs + static ARP (loop-safe).
  - Test: `pingall` = 0% drop; `ovs-ofctl` shows expected ports.
  - PASS: full connectivity, ≥3 usable paths confirmed.
  - Output: `topologies/mesh.py` + a topology diagram in `results/figures/`.

- [x] **S1.2 — Telemetry + live graph.** DONE 2026-07-01: `collector.py`
    (`TelemetryCollector` normalized [tput,loss,delay] + `TelemetryMonitor` app) and
    `topology_graph.py` (`TopologyGraph`, live `active_graph()` from static spec +
    port-status). Live test (mesh+STP+iperf): graph matches spec (7 switches, all
    ports), telemetry non-zero on active Path A. See `results/logs/s1_2_telemetry_results.txt`.
    Refactor: pure `topologies/mesh_spec.py` (Mininet-free) shared by builder + control plane.
  - Build: `framework/telemetry/collector.py` (port-stats poll + normalization,
    reused from the controllers); `framework/telemetry/topology_graph.py` (live
    switch/link graph, updated from LLDP or a static map + port-status).
  - Test: prints per-port `[throughput,loss,latency]` and a correct adjacency graph.
  - PASS: graph matches the topology; telemetry non-zero under traffic.

- [x] **S1.3 — Fault injector.** DONE 2026-07-01: `topologies/faultgen.py`
    (`FaultInjector` + `FaultLog` + YAML scheduler) — link_down/up, multi_link,
    switch_down/up (node isolation), congestion (soft UDP flood), flap. Self-test:
    all 7 types injected + logged with timestamps; link_down verified to down the
    interface. Schedule `configs/faults_demo.yaml`. See `results/logs/s1_3_faultgen_results.txt`.
  - Build: `topologies/faultgen.py` — scheduled faults: single-link, node (switch),
    congestion (UDP flood), multi-link, and flapping (up/down cycles).
  - Test: each fault type is injected + logged with a timestamp.
  - Output: reproducible fault schedules (in `configs/`).

- [ ] **S1.4 — Live traffic.**
  - Build: `topologies/traffic.py` — mixed TCP + UDP, background + foreground,
    varying rates, so **congestion ≠ fault** (not trivially separable).
  - Test: `iperf` flows run; telemetry shows realistic load.

- [ ] **S1.5 — Data collection at scale.**
  - Build: run S1.1–S1.4 together; log telemetry + ground-truth labels
    (fault/normal + which element) to a growing dataset.
  - Test: dataset has **≥1–2k rows across ≥2 topologies**, multiple fault types.
  - PASS: dataset diversity (not dominated by zero-throughput idle rows).
  - Output: `data/datasets/telemetry_v2.csv` + a label-distribution report.

- [ ] **S1.6 — Detector with localization.**
  - Build: `framework/agents/detector.py` — classify fault/normal **and output the
    failed `(dpid,port)`**. Start with the lightweight classifier (it beat PPO).
  - Test: on held-out data, predicts fault + correct location.

- [ ] **S1.7 — Detection experiment.**
  - Build: `experiments/exp_p1_detection.py` — stratified split **before** balancing,
    ≥5 seeds; report P/R/F1 **and localization accuracy**; train on some topologies,
    **test on an unseen one** (generalization).
  - PASS: **localization ≥ 90%**, **precision ≥ 0.95** (few false alarms), and F1 on
    the **unseen topology** within ~0.05 of seen.
  - Output: `docs/p1_results.txt`, `results/figures/p1_*.png`, paper subsection.
  - **GATE — do not start Phase 2 until this PASSES.**

---

# PHASE 2 — M-B: RESOLVER finds the BEST solution   [~2–3 sessions]  ← the headline
*Proves: "find the best solution." Dijkstra/Yen for candidates + NEW scoring to pick.*

- [ ] **S2.1 — Candidate path generation.**
  - Build: `framework/pathing/k_paths.py` — Yen's **k-shortest-paths** (Dijkstra
    inside) on `topology_graph`, excluding the failed element.
  - Test (unit): on a hand-drawn graph, returns the known k paths in order.
  - PASS: correct k paths; excludes the failed link/node.

- [ ] **S2.2 — Scoring + selection (the new part).**
  - Build: `framework/agents/resolver.py` — score each candidate by an objective
    `w1(−latency)+w2(capacity)+w3(−loss)+w4(−hops)`; select the best; hand ports to
    `actuation.py` to install via `OFPFlowMod`.
  - Test: given synthetic link states, picks the analytically-best path.

- [ ] **S2.3 — Baselines (upper/lower bounds).**
  - Build: `baselines/{fixed_failover,shortest_path,random_path,oracle_path}.py`
    (oracle = best path with full knowledge = upper bound).
  - Test: each returns a valid path.

- [ ] **S2.4 — Wire the closed loop.**
  - Build: `framework/controller/shield_app.py` — Ryu app: telemetry → detector →
    resolver → actuator, with failback on link-up.
  - Test (integration): on `mesh.py`, inject a fault → auto-detect → resolve →
    recover; `pingall` recovers.
  - PASS: automatic recovery on the big topology.

- [ ] **S2.5 — Best-solution experiment.**
  - Build: `experiments/exp_p2_resolver.py` — inject faults under **≥4 varying link
    conditions** (different delay/loss/bw); measure post-recovery **QoS** (throughput,
    loss, latency) for fixed-failover / shortest / random / **Resolver** / oracle; ≥5 seeds.
  - PASS: **Resolver beats fixed-failover AND shortest-path on composite QoS in ≥3 of
    ≥4 scenarios, and lands within ~10% of oracle.**
  - Output: `docs/p2_results.txt`, comparison figure/table, paper subsection.
  - **GATE — do not start Phase 3 until this PASSES.**

- [ ] **S2.6 — (Optional) RL resolver variant.**
  - Build: RL policy that *learns* the selection (real multi-action decision — where
    RL can finally help). Compare to the heuristic. Report honestly (if heuristic
    ties oracle, say the framework, not RL, is the contribution).

---

# PHASE 3 — M-C: LEARNER updates itself day-by-day   [~2–3 sessions]  ← the novelty
*Proves: "update itself and learn." Needs the disk freed (S0.1) and a non-stationary world.*

- [ ] **S3.1 — Experience store + reward.**
  - Build: `framework/experience/store.py` (SQLite: state, fault, candidates, chosen,
    outcome) + `reward.py` (`r = −recovery_time − α·post_loss + β·throughput`).
  - Test (unit): write/read a recovery record round-trip; reward math correct.

- [ ] **S3.2 — Learner loop.**
  - Build: `framework/agents/learner.py` — every N recoveries (a "day"), update the
    Resolver (and optionally Detector) from the store; hold out a **validation set**
    to detect catastrophic forgetting.
  - Test: model params change after an update; validation score computed.

- [ ] **S3.3 — Non-stationary schedule.**
  - Build: in `faultgen.py`/`traffic.py`, shift the fault/traffic distribution across
    "days" so there is genuinely something to adapt to.
  - Test: distribution differs day-to-day (logged).

- [ ] **S3.4 — Learning-curve experiment.**
  - Build: `experiments/exp_p3_online.py` — run **D days**; plot recovery-QoS vs day
    for **SHIELD-online vs a frozen model**; ≥5 seeds; track validation each day.
  - PASS: **statistically significant improvement day 1→D (p<0.05), frozen model
    stays flat, and validation score is non-decreasing** (no forgetting).
  - Output: `docs/p3_results.txt`, the **learning curve** figure, paper subsection.
  - **GATE — only now may the paper claim "learns day by day."**

---

# PHASE 4 — Integration, evaluation & publication   [~1–2 sessions]

- [ ] **S4.1 — Full end-to-end run.** One command: big topology + live traffic + fault
  schedule + detect + resolve + learn. PASS: runs clean start-to-finish, recovers +
  improves. Output: `results/logs/e2e_run.txt`.
- [ ] **S4.2 — Test suite + reproducibility.** All unit + integration tests green;
  pinned versions, fixed seeds, exact CLIs, configs committed.
- [ ] **S4.3 — Framework paper.** Extend `paper/` (or a new paper): architecture,
  the three pillar experiments, honest baselines/ablations. Every number `[VERIFIED]`.
- [ ] **S4.4 — Review + build.** Run `/peer-reviewer`; address findings; compile on
  Overleaf (no TeX on the VM).

---

## Dependency graph (what blocks what)
```
S0 → S1.1 → S1.2 ┐
       S1.3,S1.4 ┼→ S1.5 → S1.6 → S1.7(GATE) → S2.1 → S2.2 ┐
                 ┘                                S2.3 ─────┼→ S2.4 → S2.5(GATE) → S3.1 → S3.2 ┐
                                                            ┘                        S3.3 ─────┼→ S3.4(GATE) → S4
                                                                                              ┘
```
**Critical path / advisor priority:** if you can only push one thing, push to
**S2.5** (best-solution PASS) — that is what makes it a *framework*. S3.4 (learning
curve) is what makes it *novel*.

## Progress tracker
| Phase | Step | Status |
|-------|------|--------|
| 0 Foundation | S0.1–S0.4 | TODO |
| 1 Detect@scale | S1.1–S1.7 | TODO (reuse existing telemetry/eval) |
| 2 Resolver | S2.1–S2.6 | TODO |
| 3 Learner | S3.1–S3.4 | TODO |
| 4 Publish | S4.1–S4.4 | TODO |

## What already exists to reuse (don't rebuild)
- Actuation (`OFPFlowMod` install/failback): `controller/reroute_agent.py`.
- PPO decision + telemetry loop: `controller/self_healing_agent.py`.
- Fast-failover baseline: `controller/failover_agent.py`.
- Recovery/latency harness: `topology/measure_recovery.py`.
- Honest eval protocol (split-before-balance, seeds, metrics): `evaluate_agent.py`,
  `compare_methods.py`.
