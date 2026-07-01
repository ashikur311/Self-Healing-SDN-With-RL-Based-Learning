# FRAMEWORK_PLAN — AI-Driven Self-Healing SDN Framework

> Working name: **SHIELD** (Self-Healing Intelligent, Experience-Learning Defense).
> This is the *next-phase* plan that extends the completed paper (`PAPER_ROADMAP.md`)
> from a single closed-loop recovery system into a general **framework**. Read
> `PAPER_ROADMAP.md` for what already exists and is verified.

---

## 0. What we are proving (the thesis of the framework)

> *In an SDN, when a fault occurs, a Detection agent automatically finds the
> problem, a Resolver agent selects the **best** recovery among alternatives and
> applies it via OpenFlow, and the system **learns from each recovery so it gets
> better over time**.*

Three pillars, each an independent, falsifiable claim:

| Pillar | Claim | Proven today? |
|--------|-------|---------------|
| **P1 Detect** | Auto-detect + localize a fault from live telemetry | Partially (link-down only, tiny data) |
| **P2 Resolve** | Choose the *best* of several recovery paths, not a fixed one | **No — one hardcoded backup** |
| **P3 Learn** | Improve recovery quality day-by-day from outcomes | **No — frozen offline model** |

The whole plan is built so each pillar has a **baseline it must beat** and a
**pass/fail number**. If a pillar can't beat its baseline, we report that honestly
(as we did with the RL-vs-classifier ablation) — never fake the win.

---

## 1. Architecture

```
                 ┌──────────────────────── SHIELD control loop (Ryu app) ───────────────────────┐
                 │                                                                               │
  OpenFlow  ─────┤  Telemetry Collector ──► Detector (Agent 1) ──► Resolver (Agent 2) ──► Actuator│──► OFPFlowMod
  switches  ◄────┤     (port stats,           fault? + WHERE       pick BEST recovery     install  │      to OVS
   (OVS)         │      topology graph)       (localization)       path among candidates  flows    │
                 │            │                     │                     │                   │      │
                 │            └───────────── Experience Store (state, fault, action, OUTCOME) ┘      │
                 │                                     │                                             │
                 │                              Learner (offline/online) ── periodically updates ──► Agent1/Agent2
                 └───────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Agent 1 — Detector (P1)
- **In:** sliding window of normalized per-port telemetry `[throughput, loss, latency, Δthroughput…]`.
- **Out:** `fault?` (binary) **and localization** `(dpid, port)` — Agent 2 needs to know *what* failed.
- **Model:** start with the lightweight classifier (our ablation showed LR/k-NN beat PPO). Keep it swappable. The detector is retrainable by the Learner (P3).
- **Why localization is new:** the current code only says "a fault exists" (throughput→0). To *resolve* it, we must name the failed element.

### 1.2 Agent 2 — Resolver (P2) — **the core new component**
Pipeline: **candidate generation → scoring → selection → actuation**.
- **Candidate generation:** maintain a live **topology graph**; on fault, compute *k* alternate paths that avoid the failed element (k-shortest-paths).
- **Scoring:** score each candidate by an objective, e.g.
  `score = w1·(−latency) + w2·(residual_capacity) + w3·(−loss) + w4·(−hops)`.
  Two variants to compare: (a) a **heuristic/optimizer** (pick max score), and
  (b) an **RL policy** that learns the weights/selection from outcomes (this is
  where RL can finally earn its place — a real sequential/contextual decision with
  a non-trivial action set, unlike the binary reroute).
- **Selection + Actuation:** install the winning path with the existing
  `OFPFlowMod` mechanism (`controller/reroute_agent.py` already does the install).

### 1.3 Learner (P3) — **continual / day-by-day learning**
- **Experience Store:** persistent log (SQLite) of every recovery:
  `(state, fault, candidates, chosen, outcome=recovery_time, post_loss, post_throughput)`.
- **Reward:** derived from the *actual* recovery outcome, e.g.
  `r = −recovery_time_ms/1000 − α·post_loss + β·post_throughput_norm`.
- **Update loop:** every *N* recoveries (a "day"), update Agent 2's policy (and
  optionally Agent 1's detector) from the growing store; keep a held-out
  validation set to detect **catastrophic forgetting**.
- **Evidence required:** a **learning curve** — recovery quality vs cumulative
  experience — in a **non-stationary** environment (traffic/fault mix changes over
  "days"), showing it beats a frozen model. This curve *is* the P3 proof.

---

## 2. Project structure

Grow the current repo into a framework package (keep existing files; migrate gradually):

```
self_healing_sdn/
├── framework/                     # the reusable SHIELD package
│   ├── agents/
│   │   ├── detector.py            # Agent 1  (P1)
│   │   ├── resolver.py            # Agent 2  (P2): candidates→score→select
│   │   └── learner.py            # continual-learning loop (P3)
│   ├── controller/
│   │   ├── shield_app.py          # Ryu app wiring the loop together
│   │   └── actuation.py           # OFPFlowMod install/remove (from reroute_agent.py)
│   ├── telemetry/
│   │   ├── collector.py           # port-stats polling + normalization
│   │   └── topology_graph.py      # live graph for path computation + localization
│   ├── pathing/
│   │   └── k_paths.py             # k-shortest-paths candidate generation
│   └── experience/
│       ├── store.py              # SQLite (state,action,outcome) log
│       └── reward.py             # outcome → reward
├── topologies/
│   ├── triangle.py               # (existing reroute_topo) — P1 smoke tests
│   ├── mesh.py                   # ≥3 redundant paths — REQUIRED for P2
│   ├── fattree.py                # scale test (P1/P3 generality)
│   ├── faultgen.py               # scheduled fault scenarios (link/node/congestion/flap/multi)
│   └── traffic.py                # mixed TCP/UDP background+foreground
├── experiments/
│   ├── harness.py                # shared measurement (recovery time, QoS) — from measure_recovery.py
│   ├── exp_p1_detection.py       # detection+localization, multi-topology
│   ├── exp_p2_resolver.py        # best-solution vs baselines vs oracle
│   └── exp_p3_online.py          # learning curve over episodes/"days"
├── baselines/
│   ├── fixed_failover.py         # our current single-backup (P2 baseline)
│   ├── shortest_path.py          # min-hop reroute (P2 baseline)
│   ├── random_path.py            # random valid path (P2 lower bound)
│   ├── oracle_path.py            # best-by-full-knowledge (P2 upper bound)
│   └── frozen_model.py           # offline-only model (P3 baseline)
├── data/  (experience.db, datasets/)      results/  (figures/, tables/, logs/)
├── configs/  (*.yaml experiment configs)  tests/  (unit + integration)
├── paper/                          # extend the current paper / or a new framework paper
└── PAPER_ROADMAP.md  FRAMEWORK_PLAN.md
```

Migration rule: **don't rewrite working code** — wrap it. `reroute_agent.py` becomes
`framework/controller/actuation.py`; `measure_recovery.py` becomes `experiments/harness.py`.

---

## 3. Milestones — each with a build, an experiment, and a PASS/FAIL gate

> Do these **in order**. Do not start a pillar until the previous one PASSES. Each
> milestone ends with a verified result doc + a paper subsection, exactly like the
> M-items in `PAPER_ROADMAP.md`.

### M-A — Detection + localization at scale (proves P1 properly)
- **Build:** `detector.py` with localization; `mesh.py`; `traffic.py` (mixed TCP/UDP so congestion ≠ fault); `faultgen.py` (link, node, congestion). Grow the dataset to ≥1–2k rows across ≥2 topologies.
- **Experiment (`exp_p1_detection.py`):** stratified split *before* balancing, ≥5 seeds; report P/R/F1 **and localization accuracy**; test generalization on a topology held out of training.
- **Baselines:** loss-threshold rule, logistic regression, k-NN (already have).
- **PASS:** localization ≥ 90%, precision ≥ 0.95 (few false alarms), and F1 on the **unseen topology** within ~0.05 of seen. If a simple classifier wins, keep it — that's fine, detection isn't where novelty must live.

### M-B — Resolver picks the BEST solution (proves P2 — the key novelty)
- **Build:** `k_paths.py`, `resolver.py` (heuristic + RL variant), `topology_graph.py`; `mesh.py` must expose **≥3 disjoint recovery paths**.
- **Experiment (`exp_p2_resolver.py`):** inject faults under **varying link conditions** (delay/loss/bandwidth set per scenario). Compare recovery **QoS** (post-recovery throughput, loss, latency) for: fixed-failover, shortest-path, random, **Resolver**, and **oracle** (upper bound).
- **PASS:** Resolver beats fixed-failover and shortest-path on a composite QoS score in **≥3 of ≥4 scenarios**, and lands within ~10% of oracle. This is the experiment that proves "finds the best solution."
- **Risk:** if the heuristic already hits oracle, the RL variant may add nothing — report honestly; the *framework* (candidate→score→select) is still the contribution.

### M-C — Day-by-day learning (proves P3 — the hardest, most novel claim)
- **Build:** `experience/store.py`, `reward.py`, `learner.py`; a **non-stationary** scenario schedule in `faultgen.py`/`traffic.py` (fault/traffic distribution shifts across "days"; a "day" = an episode block).
- **Experiment (`exp_p3_online.py`):** run *D* days; each day the Learner updates Agent 2 from accumulated experience. Plot **recovery quality vs day** for SHIELD (online) vs a **frozen** model. Track a held-out validation score to prove no catastrophic forgetting.
- **PASS:** statistically significant upward trend (e.g., recovery-QoS improves ≥X% from day 1→D, p<0.05 over ≥5 seeds) **and** frozen baseline does *not* improve, **and** validation score non-decreasing. Only then may the paper claim "learns day by day."
- **Risk (be upfront):** this is a genuine continual-learning result. If improvement isn't significant, the honest claim downgrades to "supports online updates" — do **not** overclaim a learning curve that isn't there.

---

## 4. How the work flows (the loop to repeat for every milestone)

1. **Design the experiment first** — write down the metric, baseline, and PASS
   threshold *before* coding. (Prevents moving goalposts.)
2. **Build the component in isolation** with a unit test (mock datapath / synthetic graph).
3. **Integrate** into `shield_app.py`; integration test = bring up topology, assert connectivity, inject one fault, assert recovery < threshold.
4. **Run the experiment** ≥5 seeds via `harness.py`; write raw results to `results/logs/`.
5. **Analyze + plot** (`results/figures/`, `results/tables/`); mark every number
   `[VERIFIED <script>]` or `[TODO]`.
6. **Gate:** PASS → write the paper subsection + a `docs/*_results.txt`; update this
   plan's status. FAIL → report honestly, decide fix-or-descope.
7. Only now start the next milestone.

**Integrity rules (non-negotiable, same as the skills):** split before balancing;
report mean±std over ≥5 seeds; every claim traces to a script; flag heuristic labels;
if a baseline beats the fancy method, say so. Use `/peer-reviewer` at the end of each
pillar and a full pass before submission.

---

## 5. Testing strategy (what "tested" means here)

- **Unit tests (`tests/`):** k-paths correctness on known graphs; reward math;
  telemetry normalization; actuation builds the right `OFPFlowMod` (mock datapath);
  experience-store round-trip.
- **Integration tests:** per topology — `pingall` works, inject fault, assert
  automatic recovery + failback; run under each fault type in `faultgen.py`.
- **System/experiment tests:** the three `exp_p*` scripts with statistical rigor.
- **Regression:** keep `measure_recovery.py` as a smoke test that recovery still
  works after refactors.
- **Reproducibility:** pin versions (Ryu 4.34 / OVS 2.13 / OF1.3 / SB3), fixed
  seeds, exact CLI in each result doc, config in `configs/*.yaml`.

---

## 6. Feasibility on THIS VM (honest constraints)

- ✅ Mininet + OVS 2.13 + Ryu 4.34 + PPO/SB3 all work here (proven this session).
- ⚠️ **Disk is 100% full**; scikit-learn/scipy would not install. Keep baselines
  pure-NumPy; SQLite is fine and light. **Free disk before M-C** (models + logs grow).
- ⚠️ **Scale:** keep topologies modest (mesh ~6–8 switches, small fat-tree). A
  "day" = a compressed episode block, not literal 24h.
- ⚠️ **Compute:** online retraining every "day" must stay small (few-k steps) to run
  in reasonable wall-clock; budget for it.
- These constraints shape scope, not feasibility — all three pillars are testable
  here at a modest scale sufficient for a paper.

---

## 7. Suggested sequencing (advisor's call)

1. **M-A** (2–? sessions): scale data + topologies + localization. Also clears paper
   reviewer item **M4**. Lowest risk, unblocks everything.
2. **M-B** (the headline): multi-path Resolver + best-solution experiment. This is
   where the *framework* becomes real and where RL gets a fair shot.
3. **M-C** (the ambitious differentiator): online learning + learning curve.
4. Re-run `/peer-reviewer`; decide: extend the current paper into a framework paper,
   or write a second paper focused on P2+P3.

**One-line advice:** build **M-B first if you must pick one thing** to make the
"framework" claim true — a Resolver that provably picks the best of several paths is
the minimum that separates "a self-healing demo" from "a self-healing *framework*."
P3 (day-by-day learning) is what makes it *novel*; P1 is already essentially done.

---

## 8. Status tracker (update as you go)

| Milestone | Component(s) | Experiment | PASS metric | Status |
|-----------|--------------|------------|-------------|--------|
| M-A Detect@scale | detector, mesh, traffic, faultgen | exp_p1_detection | localization≥90%, generalizes | TODO |
| M-B Resolver | k_paths, resolver, graph | exp_p2_resolver | beats failover/shortest, ~oracle | TODO |
| M-C Online learn | store, reward, learner | exp_p3_online | sig. learning curve, no forgetting | TODO |
