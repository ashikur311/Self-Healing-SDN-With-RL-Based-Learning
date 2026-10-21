# Self-Healing SDN  

Autonomous fault recovery in Software-Defined Networking: the controller reads live
OpenFlow telemetry, detects a link fault, and **automatically reprograms the data
plane (`OFPFlowMod`) to reroute traffic** onto a healthy path — a real closed loop,
not a decision-only prototype.

> **New here? Start with [`docs/TEAM_GUIDE.md`](docs/TEAM_GUIDE.md)** — a
> beginner-friendly, step-by-step walkthrough of what we built, which file connects
> to which, and where each result lives. The complete FYDP report is
> [`paper/fydp_report_full.tex`](paper/fydp_report_full.tex) (all chapters, completed
> Standards & Design Constraints, and the real experiments). A shorter IEEE-style
> version is [`paper/final.tex`](paper/final.tex) (quick preview: open
> [`paper/final_preview.html`](paper/final_preview.html) and Print → Save as PDF).

- **Phase 1 (done):** the closed-loop recovery system + an honest, verified paper.
  Final Year Design Project (UIU, CSE, Group B1) — the project was **successful**.
- **Phase 2 (planned):** grow it into a general AI framework — bigger topology,
  a Resolver agent that picks the **best** of several recovery paths, and a Learner
  that **improves day by day**. See the planning docs below.

## Repository map

```
├── controller/          # Active Ryu apps (OpenFlow 1.3)
│   ├── reroute_agent.py        # event-driven reroute (fast baseline)     ~40 ms recovery
│   ├── self_healing_agent.py   # PPO-decision-driven reroute (main system)
│   └── failover_agent.py       # OpenFlow fast-failover (OFPGT_FF) baseline
├── topology/            # Mininet topologies + measurement harness + tests
│   ├── reroute_topo.py         # triangle topology with a backup link
│   ├── measure_recovery.py     # recovery-time + data-plane RTT/jitter harness
│   └── test_reroute*.py        # pass/fail recovery tests
├── train_agent.py       # PPO training + SDN gym env (also a sanity check)
├── evaluate_agent.py    # Honest held-out evaluation (split-before-balance, 5 seeds)
├── compare_methods.py   # PPO vs simple baselines (loss-rule, logistic, k-NN)
├── dataset/             # network_data.csv (telemetry)
├── sdn_recovery_agent.zip   # trained PPO model (loaded by the controllers)
├── docs/                # Verified result artifacts + figures + extracted report text
├── paper/               # IEEEtran paper source (main.tex, references.bib, figures/)
├── archive/             # Retired "before" files (see archive/README.md)
└── .claude/skills/      # 6 expert-reviewer skills used to harden the paper
```

## Planning / status docs
- **`PAPER_ROADMAP.md`** — the finished paper: status, every fix + reviewer item, verified numbers.
- **`FRAMEWORK_PLAN.md`** — Phase-2 architecture (the 3-pillar "SHIELD" framework) and *why*.
- **`project_working_step.md`** — Phase-2 step-by-step execution checklist (do-this-next).

## Quick start (reproduce the recovery result)
```bash
# Terminal A — start the event-driven controller
/home/mininet/venvs/ryu-4.34-py39/bin/ryu-manager controller/reroute_agent.py

# Terminal B — run the recovery + latency measurement (needs root)
sudo python3 topology/measure_recovery.py 5 2.0
```
See each `docs/*_results.txt` header for the exact reproduce command per experiment.

## Environment
Ubuntu VM · Mininet · Open vSwitch 2.13 · OpenFlow 1.3 · Ryu 4.34 (py39 venv) ·
Stable-Baselines3 (PPO).
