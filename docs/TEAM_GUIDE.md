# Self-Healing SDN — Team Guide (Beginner Friendly)

This document explains, in plain language, **what we built, how the pieces fit
together, and which file does what.** If you are new to the project, read this
top to bottom once. No deep networking or machine-learning background needed.

---

## 1. The idea in one paragraph

A network is a set of switches passing traffic along links. Links fail. Our
system is a **controller** (a program that supervises the switches) that heals the
network by itself. It does four things in a loop:

1. **Detect** — notice a link has failed and find *which* link.
2. **Resolve** — pick the best surviving path around the failure.
3. **Solve (act)** — actually reprogram the switches to use that path.
4. **Learn** — remember how well each recovery worked and get better day by day.

That four-step loop is the whole project. Everything below maps to one of those
steps.

---

## 2. The tools we use (so the code makes sense)

| Tool | What it is | Why we use it |
|------|-----------|---------------|
| **Mininet** | Software that emulates a real network on one computer | Lets us build switches, links, and hosts without hardware |
| **Open vSwitch (OVS)** | A software switch | The switches in our emulated network |
| **OpenFlow** | The language the controller uses to talk to switches | Lets us read stats and install forwarding rules |
| **Ryu** | An OpenFlow controller framework (Python) | The base our controller is built on |
| **Python** | Programming language | Everything is written in Python |

Note: two Python versions are used. Mininet runs on the system Python (3.8, via
`sudo`); the controller and learning code run in a virtual environment
(`/home/mininet/venvs/ryu-4.34-py39`, Python 3.9). See `docs/env.txt`.

---

## 3. Folder map (where things live)

```
self_healing_sdn/
├── framework/        ← THE BRAIN. The reusable self-healing components.
│   ├── telemetry/    ← reads switch statistics
│   ├── agents/       ← detect, localize, resolve, learn logic
│   ├── pathing/      ← finds candidate paths
│   ├── experience/   ← stores outcomes + computes reward
│   └── controller/   ← the live Ryu apps that run on the network
├── topologies/       ← the networks we build + fault/traffic generators (new)
├── topology/         ← the ORIGINAL small topology + recovery tests (older)
├── baselines/        ← the competing strategies we compare against
├── experiments/      ← the scripts that RUN each experiment and produce numbers
├── controller/       ← the original controller (RL agent + reroute)
├── tests/            ← unit tests (fast, no network needed)
├── configs/          ← YAML settings for faults and traffic
├── data/, dataset/   ← the datasets used for fault detection
├── docs/             ← RESULTS of every experiment (the proof behind the paper)
├── results/          ← tables (CSV) and raw run logs
├── paper/            ← the research papers (see section 8)
├── train_agent.py, evaluate_agent.py, compare_methods.py  ← original ML scripts
├── REPRODUCE.md      ← exact commands to re-run everything
└── FRAMEWORK_PLAN.md, PAPER_ROADMAP.md, project_working_step.md  ← plans/tracking
```

**Rule of thumb:** `framework/` = the components, `experiments/` = the scripts that
run them and generate results, `docs/` = the results, `paper/` = the write-up.

---

## 4. The pipeline, step by step (which file connects to which)

Below, each step lists: **what it does → the main file(s) → the experiment that
tests it → where it appears in the paper.**

### Step 1 — DETECT (notice + locate the fault)
- **What:** read per-port statistics, decide a fault happened, and find the exact
  failed link. We learned telemetry alone cannot pinpoint the link, so we use
  OpenFlow *port-status* for the location and telemetry for "is it hurting traffic."
- **Files:**
  - `framework/telemetry/collector.py` — turns raw switch counters into
    `[throughput, loss, delay]`.
  - `framework/agents/detector.py` — classifier: is this a fault? (telemetry)
  - `framework/agents/localizer.py` — **which** link failed (port-status). Exact.
  - `framework/controller/detect_app.py` — the live Ryu app that runs detection.
- **Experiment:** `experiments/exp_p1_detection.py`
- **Result:** `docs/p1_detection_results.txt` (19/19 faults located, 0 false alarms)
- **Paper:** Section "Telemetry and state", "Fault-detection accuracy",
  "Localization at scale".

### Step 2 — RESOLVE (choose the best surviving path)
- **What:** after a link dies, list the surviving paths and score each one on
  latency, capacity, loss, and hop count; pick the best.
- **Files:**
  - `framework/pathing/k_paths.py` — lists candidate paths (Yen's algorithm).
  - `framework/agents/resolver.py` — **scores paths and picks the best.**
  - `baselines/*.py` — the competitors: `shortest_path.py`, `fixed_failover.py`,
    `widest_path.py`, `random_path.py`, `oracle_path.py`.
- **Experiment:** `experiments/exp_p2_resolver.py`
- **Result:** `docs/s2_5_results.txt`, `results/tables/p2_resolver_qos.csv`
- **Paper:** Section "From decision to recovery path", "Choosing the recovery path".

### Step 3 — SOLVE / ACT (reprogram the switches)
- **What:** install the new forwarding rules (`OFPFlowMod`) so traffic uses the
  chosen path. This is the part that actually fixes the network.
- **Files:**
  - `framework/controller/shield_core.py` — the decision "brain" (no Ryu needed).
  - `framework/controller/shield_app.py` — installs the rules on the live switches.
  - `controller/reroute_agent.py` — the original event-driven reroute (~40 ms).
  - `topology/measure_recovery.py` — measures how fast recovery is.
- **Experiment:** `experiments/exp_p2_recovery.py`
- **Result:** `docs/recovery_latency_results.txt`, `docs/s2_4_results.txt`
- **Paper:** Section "End-to-end recovery", "Recovery mechanism vs. an OpenFlow
  baseline".

### Step 4 — LEARN (get better day by day)
- **What:** score each recovery with a reward (more throughput, less loss = better),
  store it, and re-tune the resolver's weights from recent experience. When the
  network changes, it re-learns the best path.
- **Files:**
  - `framework/experience/reward.py` — computes the reward for a recovery.
  - `framework/experience/store.py` — remembers past recoveries (a small database).
  - `framework/agents/learner.py` — **re-tunes the resolver from experience.**
  - `topologies/nonstationary.py` — the "drifting world" where the best path changes.
- **Experiment:** `experiments/exp_p3_online.py`
- **Result:** `docs/s3_4_results.txt`, `results/tables/p3_learning_curve.csv`
- **Paper:** Section "Learning from outcomes", "Learning on a changing network".

### All four together (one live run)
- **File:** `experiments/exp_e2e.py`
- **Result:** `docs/s4_1_results.txt` (20/20 faults handled; the learner adapts)
- **Paper:** Section "Putting the loop together: one live run".

---

## 5. How the data flows (the loop, drawn in text)

```
        ┌─────────────────────────────────────────────────────────┐
        │                                                         │
        ▼                                                         │
  Telemetry &      →   Detect &   →   Resolve    →   Actuate      │
  port-status          localize       best path      OFPFlowMod   │
 (collector.py)     (localizer.py)  (resolver.py)  (shield_app.py)│
                                        ▲                │        │
                                        │                ▼        │
                                    Learn (re-fit)   Data plane   │
                                   (learner.py)      (OVS mesh) ──┘
                                        ▲                │
                                        └── reward ──────┘
                                        (reward.py + store.py)
```

Read it as a circle: the network is watched, a fault is found and located, a path
is chosen and installed, the result is scored into a reward, and the reward teaches
the resolver to choose better next time.

---

## 6. The honest headline (important for questions/defense)

Our own results show that **the value is the integrated loop, not a fancy learning
algorithm.** Two facts we report openly:
- For *detecting* faults, a simple classifier is as good as (or better than) the
  reinforcement-learning agent, so we keep detection simple.
- The reinforcement learning matters in the *recovery* stage, where the controller
  learns from rewards and adapts as the network drifts (Step 4). That is the part
  the paper title "using Reinforcement Learning" points to.

If someone asks "is this really reinforcement learning?" — yes, in Step 4: the
learner acts, gets a reward, and improves. Detection is deliberately kept as simple
classification because learning is not needed there.

---

## 7. Running things (quick start)

The one source of truth for exact commands is **`REPRODUCE.md`**. In short:

- **Fast unit tests (no network, ~1 second):**
  `python3 tests/run_all.py`
- **A live experiment (needs root + Mininet), example:**
  `sudo python3 -u experiments/exp_p2_resolver.py > results/logs/out.log 2>&1`

Live experiments must be run as `.py` files with `sudo`, not typed line by line
(see `docs/` notes on Mininet quirks). Every experiment writes a result file into
`docs/` and/or a table into `results/tables/`.

---

## 8. The papers (what to submit)

There are **two** paper files in `paper/`:

| File | What it is | Use it? |
|------|-----------|---------|
| `paper/main.tex` | The original FYDP thesis paper (registered) | Keep as-is, do not edit |
| `paper/final.tex` | **The complete submission paper** (thesis + all new work) | **This is the one we submit** |

`final.tex` keeps the registered title, has all six authors + advisor, four figures,
and every number is tagged `% [VERIFIED ...]` pointing to a file in `docs/`.

Supporting files: `paper/references.bib` (the citations) and `paper/figures/` (the
images). `paper/final_preview.html` is a quick browser preview you can print to PDF.

---

## 9. How to make the real PDF (Overleaf)

This computer has no LaTeX installed, so the official PDF is built on Overleaf:

1. Go to https://overleaf.com and create a **New Project → Blank Project** (or
   "Upload Project").
2. Upload these from the `paper/` folder:
   - `final.tex`
   - `references.bib`
   - the whole `figures/` folder (keep the folder name `figures`).
3. Set the main document to `final.tex`.
4. Overleaf compiles automatically (it runs pdfLaTeX → BibTeX → pdfLaTeX twice).
   Click **Recompile**, then **Download PDF**.

For a quick look without Overleaf, open `paper/final_preview.html` in a browser and
choose Print → Save as PDF.

---

## 10. Mini glossary

- **SDN** — Software-Defined Networking: a controller programs the switches centrally.
- **Controller** — the program that watches and reprograms the network (that's us).
- **Telemetry** — the statistics (throughput, loss, delay) read from switches.
- **Localization** — figuring out *which* link failed.
- **Reroute / Resolve** — choosing and installing a new path.
- **`OFPFlowMod`** — the OpenFlow message that changes a switch's forwarding rules.
- **Reward** — a single number saying how good a recovery was (used for learning).
- **Non-stationary / drift** — the network changes over time, so the best path changes.
- **Baseline** — a simpler competing method we compare our system against.
- **Oracle** — an ideal method with perfect information; used as a reference, not a
  competitor.

---

*Questions? Start from the file named in the step you care about, then open its
experiment in `experiments/` and its result in `docs/`. Those three always match.*
