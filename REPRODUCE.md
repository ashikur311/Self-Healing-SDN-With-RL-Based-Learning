# Reproducing SHIELD (Self-Healing SDN framework)

Everything needed to re-run the framework's experiments and tests. Every reported
number traces to a committed script + a log under `results/logs/` and a summary in
`docs/*_results.txt`. Seeds are fixed in-script; versions are pinned below.

## 1. Environment (pinned — see `docs/env.txt`)

Two interpreters (by design):

| Role | Interpreter | Key pins |
|------|-------------|----------|
| Mininet data plane (run via `sudo`) | system **Python 3.8.10** | Open vSwitch 2.13.8, OpenFlow 1.3 |
| Ryu controller + RL venv | `/home/mininet/venvs/ryu-4.34-py39` (**Python 3.9.5**) | ryu-manager 4.34, stable_baselines3 2.7.1, torch 2.8.0+cpu, gymnasium 1.1.1 |

The offline framework code (pathing / resolver / baselines / experience / learner /
schedule) is **stdlib-only** and runs under the system Python 3.8 with no venv.

## 2. Offline unit tests (no sudo, no Mininet) — the CI gate

```bash
python3 tests/run_all.py          # runs all 8 offline suites; exits non-zero on any fail
```
Expected: `8/8 suites PASS ... *** OFFLINE TEST SUITE: GREEN` (~0.6s).

Individual suites (each prints `... PASS`): `test_k_paths` (S2.1), `test_resolver`
(S2.2), `test_baselines` (S2.3), `test_shield_core` (S2.4), `test_localizer` (S1.6),
`test_experience` (S3.1), `test_learner` (S3.2), `test_nonstationary` (S3.3).

## 3. Live experiments (need `sudo` + Mininet)

Run each as a committed `.py` FILE with output redirected (see the Mininet-sandbox
notes: no `&`, no heredocs, no `pkill -f <token-in-cmdline>`). `sudo mn -c` between
runs is fine and each script self-cleans.

```bash
# S1.1 topology self-test — pingall 0%, 3 node-disjoint paths
sudo python3 topologies/mesh.py --test

# S1.2 telemetry live (needs the Ryu app on :6653 first, from the venv):
#   /home/mininet/venvs/ryu-4.34-py39/bin/ryu-manager framework/telemetry/collector.py &
sudo python3 tests/s1_2_telemetry_live.py

# S1.7 detection + localization  (GATE: localization >=0.90, congestion FA==0)
sudo python3 -u experiments/exp_p1_detection.py            > results/logs/p1_detection.log 2>&1

# S2.4 closed-loop recovery       (GATE: auto detect->resolve->recover, pingall recovers)
sudo python3 -u experiments/exp_p2_recovery.py            > results/logs/s2_4_recovery.log 2>&1

# S2.5 best-solution resolver     (GATE: SHIELD beats fixed+shortest in >=3/4, ~oracle)
sudo python3 -u experiments/exp_p2_resolver.py           > results/logs/s2_5_resolver.log 2>&1

# S3.4 online learning curve      (GATE: online>frozen p<0.05, frozen degrades, no forgetting)
sudo python3 -u experiments/exp_p3_online.py             > results/logs/s3_4_online.log 2>&1

# S4.1 full end-to-end run        (detect+resolve+learn; recovers + adapts)
sudo python3 -u experiments/exp_e2e.py                   > results/logs/e2e_run.txt 2>&1
```

### Seeds (fixed, in-script)
- `topologies/nonstationary.py` schedule seed = **7**; per-day/rep draws seeded by
  `(seed, day, rep)`.
- `exp_p2_resolver.py`: SEEDS=5 (repeated live measurements). `exp_p3_online.py`:
  SEEDS=5, D_DAYS=9, WINDOW_DAYS=2, EPSILON=0.25. `exp_e2e.py`: D=5, 4 recoveries/day.
- Baseline/traffic RNGs seeded explicitly at each call site.

### Provenance / honesty notes
- `exp_p3_online.py` (S3.4) QoS anchors are **measured live** on the mesh; the D-day
  loop is trace-driven from those measurements (a fully-live D-day run is future
  work). See `docs/s3_4_results.txt`.
- Known review debt (fix before the framework paper, S4.3): `docs/p2_review_notes.md`
  (S2.5 constructed comparison) and `docs/p3_review_notes.md` (S3.4 evaluation).

## 4. Where results live
- Per-step summaries: `docs/s*_results.txt`, `docs/p*_results.txt`.
- Raw logs: `results/logs/` (git-ignored). Tables: `results/tables/*.csv`.
- Configs: `configs/faults_demo.yaml`, `configs/traffic_default.yaml`.
- Progress + PASS gates: `project_working_step.md`; architecture: `FRAMEWORK_PLAN.md`.
