# PAPER_ROADMAP — Self-Healing SDN → Publishable Paper

> **New session? Read ONLY this file first.** It is the single source of truth for
> status and next steps. Do **not** re-extract the PDF or re-read all the code to
> get oriented — everything you need to resume is here. Deep-dive into a specific
> file only when the step you're doing requires it.

---

## 0. Snapshot (token-cheap orientation)

- **Project:** RL-based self-healing SDN. Agent 1 (Ryu) reads OpenFlow telemetry →
  state `[throughput, loss, delay]`; Agent 2 (PPO) decides `0=do nothing / 1=reroute`.
- **Stack:** Mininet + Open vSwitch + OpenFlow 1.3 + Ryu + Stable-Baselines3, on this Ubuntu VM.
- **Goal:** make it a **publishing-ready paper** with full research integrity
  (no fabricated results/citations; overclaims flagged, not smoothed over).
- **Report text (don't re-parse the PDF):** `docs/fydp_report_text.txt`.
- **Expert skills (invoke after a Claude Code restart):** `.claude/skills/` — see table in §4.

## 1. Key files

| Path | What it is |
|------|-----------|
| `docs/fydp_report_text.txt` | Full extracted thesis text (34 pp). Read instead of the PDF. |
| `train_agent.py` | PPO training + the SDN gym env (stateless — see fix #4). |
| `test_agent.py` | 3-scenario sanity test (NOT a real evaluation — see fix #2). |
| `controller/monitor_agent.py` | Live Ryu Agent 1+2: polls stats, runs PPO, **only prints** decisions. |
| `topology/custom_topo.py` | Original **linear** topology (no reroute possible). |
| `topology/reroute_topo.py` | **NEW (fix #1):** redundant topology that makes reroute possible. |
| `controller/reroute_agent.py` | **NEW (fix #1):** installs real OFPFlowMod backup path on link failure. |
| `topology/test_reroute.py` | **NEW (fix #1):** automated pass/fail harness that proves recovery (non-interactive). |
| `dataset/network_data.csv` | 133-row telemetry dataset (too small — see fix #2/#5). |

## 2. Self-imposed peer-review (why we're doing this)

> **Context:** This is a **3-trimester FYDP that the team WON within its first 2
> trimesters** — a successful project, not a rejected one. The 3rd-trimester goal is
> to publish it as a paper, now backed by a REAL auto-actuating self-healing agent.

To harden the paper before a real submission we ran our own `/peer-reviewer` skill —
a **simulated** adversarial "Reviewer 2." Its verdict on the *initial* draft was
**Reject / Major revision** (NOT a real journal verdict, NOT a verdict on the FYDP).
Root problems it flagged: the "self-healing" recovery was decision-only, the method
was labeled RL but is really classification, and quantitative claims ("100%
accuracy") were unsupported/leaky. **All have since been fixed** (fixes #1–#7 +
reviewer items M1–M3, M5 done; only M4 remains). Re-run `/peer-reviewer` for a fresh
verdict after M4.

## 3. The fix pipeline — do in order, one step per work session

Each step is self-contained so a fresh session can pick up exactly one and finish it.
Update the **Status** column when you finish a step.

| # | Fix | Owner skill | Status |
|---|-----|-------------|--------|
| **1** | **Real OFPFlowMod reroute on a redundant topology** | sdn-expert + network-engineer | **✅ DONE + verified (2026-07-01) — event-driven AND PPO-driven both PASS** |
| 2 | Honest evaluation: split BEFORE oversampling, confusion matrix, P/R/F1, ≥5 seeds | ml-rl-researcher | **✅ DONE 2026-07-01 (see §6)** |
| 3 | Add ≥2 baselines (packet-loss threshold, logistic, k-NN) + comparison table | ml-rl-researcher + network-engineer | **✅ DONE 2026-07-01 (see §7)** |
| 4 | Reframe method honestly (real MDP **or** call it classification/bandit); align all wording | ml-rl-researcher + academic-writer | **✅ DONE 2026-07-01 — reframed in `paper/main.tex` (see §8)** |
| 5 | Fix "delay" metric (it's controller RTT, not data-plane latency); add real latency/jitter | network-engineer | **✅ DONE + verified (2026-07-01) — see §9** |
| 6 | Verify/fix every citation; rebuild related-work table; produce `references.bib` | literature-reviewer | **✅ DONE + verified (2026-07-01) — see §10** |
| 7 | Grammar pass, drop Gantt/cost, remove phantom action 2, honest abstract, IEEE LaTeX skeleton | academic-writer | **✅ DONE (2026-07-01) — see §11. Only external Overleaf compile-check remains (no TeX on VM)** |

> Practical blocker for "publishing-ready": there is **no editable source**, only the
> PDF. Step 7 includes creating an IEEEtran LaTeX skeleton so edits are durable.

## 4. The expert skills (who to invoke for what)

| Invoke | Use for |
|--------|---------|
| `/ml-rl-researcher` | PPO design, reward/state/action, evaluation rigor, baselines, ablations |
| `/sdn-expert` | Ryu/OpenFlow/Mininet control plane, the OFPFlowMod reroute |
| `/network-engineer` | topology realism, metric definitions, fault injection, measurement |
| `/academic-writer` | prose, grammar, abstract, contributions, IEEE structure, LaTeX |
| `/literature-reviewer` | citations, BibTeX, related-work positioning |
| `/peer-reviewer` | adversarial full-paper review + prioritized fix list |

(Skills are project files; Claude Code loads them at startup — restart to make `/name` work.)

---

## 5. Fix #1 — detailed sub-steps + progress

**Why:** the paper's whole promise ("self-healing") requires the controller to actually
change flows to recover traffic. Two blockers: (a) it only prints, no `OFPFlowMod`;
(b) the linear topology has no alternate path to reroute onto.

**Design chosen (loop-safe, demo-able):** triangle of 3 switches with a direct backup
link `s1–s3`. Primary path `h1–s1–s2–s3–h3`; on failure, install higher-priority flows
onto the direct `s1–s3` link. Forwarding is 100% explicit proactive flows and ARP is
static, so the physical loop causes **no broadcast storm** (this is why we don't reuse
SimpleSwitch13 flooding here).

| Sub-step | What | Status |
|----------|------|--------|
| 1.1 | `topology/reroute_topo.py` — triangle topo, fixed MACs, static ARP, explicit ports | DONE + verified |
| 1.2 | `controller/reroute_agent.py` — proactively install primary h1↔h3 flows | DONE + verified |
| 1.3 | reroute_agent — detect primary-link down → OFPFlowMod onto backup s1–s3 | DONE + verified |
| 1.4 | **Run + verify on the VM** via `topology/test_reroute.py` | **DONE 2026-07-01 — PASS (see result below)** |
| 1.5 | Wire PPO Agent 2 `action==1` to call `trigger_reroute()` — `controller/self_healing_agent.py` (Agent1 telemetry + Agent2 PPO + reroute). `reroute_agent.py` kept as event-driven baseline for ablation. | **DONE 2026-07-01 — PASS** |

**Sub-step 1.5 verified result (2026-07-01), `controller/self_healing_agent.py` + `topology/test_reroute_ppo.py`:**
- Healthy traffic → PPO says action=0 (do nothing). After `s2-s3` down, telemetry drops to
  throughput 0 → PPO outputs **action=1** on the failed primary port
  (`state=[0.0, 1.0, ~0.05]`) → controller logged `[ACTUATE] ... triggered by PPO decision`.
- Post-reroute ping: **0% loss**. Exactly one actuation (`on_backup` guard suppresses repeats).
- **VERDICT: PASS** — PPO decision genuinely drives the OFPFlowMod recovery.
- Side note for paper fix #5: the state's 3rd value (~0.05) is controller RTT, not data-plane
  latency — confirms the delay-metric relabeling needed later.

### ✅ Fix #1 COMPLETE. Two controllers now exist (useful as a paper ablation):
- `controller/reroute_agent.py` — event-driven reroute (fast, deterministic baseline).
- `controller/self_healing_agent.py` — PPO-decision-driven reroute (the paper's main system).
Next up in the pipeline: **Fix #2 (honest evaluation)** — reuse `topology/test_reroute*.py`
to measure recovery time / packet loss across runs, and fix the train/test split in `train_agent.py`.

**Verified result (2026-07-01), `sudo python3 topology/test_reroute.py` with `reroute_agent.py` running:**
- Primary-path ping h1→h3: **0% loss**.
- After `s2-s3` link down: controller logged `[DETECT] primary link down at s3 port 2` →
  `[FAULT] ... installed BACKUP path s1-s3 (OFPFlowMod)`; post-fault ping: **0% loss**.
- **VERDICT: PASS** — real self-healing reroute works. Reproduce: start controller
  (`/home/mininet/venvs/ryu-4.34-py39/bin/ryu-manager controller/reroute_agent.py`),
  then `sudo python3 topology/test_reroute.py`.

### How to run/verify fix #1 (run these on the VM; prefix with `!` in Claude Code)
```bash
# Terminal A — start the reroute controller
cd /home/mininet/self_healing_sdn
ryu-manager controller/reroute_agent.py

# Terminal B — start the topology (needs sudo)
sudo python3 topology/reroute_topo.py
#   mininet> h1 ping h3          # should work via primary path (through s2)
#   mininet> link s2 s3 down     # kill the primary path
#   ... ping should recover automatically via the direct s1-s3 backup ...
#   mininet> link s2 s3 up       # restore
```
**Expected:** ping to h3 keeps working (a few lost packets during switchover), and the
controller logs `[FAULT] primary path down -> installing backup path s1-s3`.

### Note on honesty
Until sub-step 1.4 passes on the VM, the reroute code is **written but unverified** —
do not report it as a working result in the paper. Mark evaluation numbers `[TO RUN]`.

---

## 6. Fix #2 — honest evaluation (DONE 2026-07-01)

**New artifacts:** `evaluate_agent.py` (protocol), `docs/eval_results.txt`,
`docs/confusion_matrix.png`. Reproduce:
`/home/mininet/venvs/ryu-4.34-py39/bin/python evaluate_agent.py`.

**Protocol:** stratified 70/30 split performed BEFORE oversampling (oversample
train only) → train PPO → evaluate on untouched test → 5 seeds, report mean±std.

**Held-out results (replaces the indefensible "100% accuracy"):**
> ⚠️ Numbers below UPDATED by M5 (2026-07-01) after fixing the action space to
> `Discrete(2)` (dropped the phantom "Block User" action). Original Discrete(3)
> values were acc 0.960±0.025 / rec 0.952±0.031 / F1 0.975±0.016, confusion FN 8 /
> TP 157. Current verified numbers:

| metric | value |
|--------|-------|
| accuracy | 0.940 ± 0.012 |
| precision | 1.000 ± 0.000 |
| recall | 0.927 ± 0.015 |
| F1 | 0.962 ± 0.008 |

Summed confusion (5 folds): TN 35, FP 0, FN 12, TP 153 → never false-alarms;
misses a few hard HIGH-congestion faults.

**Key honesty finding (drives fix #4):** the "zero-throughput ⇒ LINK_DOWN" rule
relabels **68 of 110 "fault" rows** — most of the positive class is idle/zero-
throughput, only 42 are real HIGH-congestion faults. So near-perfect scores
reflect an almost linearly separable, heuristically-labeled task, not a hard
learning problem. The paper must state this; it also predicts fix #3's simple
baselines will match the PPO agent. `train_agent.py` was patched: its 3-scenario
block is now labeled a SANITY CHECK, not an evaluation.

**Next: Fix #3** — add ≥2 baselines (packet-loss threshold rule, logistic
regression / random forest) on the SAME split and build the comparison table;
this needs `scikit-learn` installed into the venv.

---

## 7. Fix #3 — baselines + comparison (DONE 2026-07-01)

**New artifacts:** `compare_methods.py`, `docs/baseline_comparison.txt`,
`docs/baseline_comparison.png`. Same stratified splits/seeds as fix #2.
Note: scikit-learn/scipy would NOT fit on this VM (disk 100% full; pip cache
purged to recover ~50 MB), so baselines are implemented in pure NumPy — this is
documented and reproducible.

**Held-out F1 (mean±std, 5 seeds):**
| method | accuracy | precision | recall | F1 |
|--------|----------|-----------|--------|----|
| Loss-threshold rule (naive static) | 0.695 | 1.000 | 0.630 | 0.773 |
| Logistic Regression | 0.960 | 1.000 | 0.952 | 0.975 |
| k-NN (k=5) | 1.000 | 1.000 | 1.000 | **1.000** |
| PPO agent (proposed) | 0.940 | 1.000 | 0.927 | 0.962 | ← M5: Discrete(2), now BELOW both baselines |

**Takeaway (feeds fix #4 — this must go in the paper):**
- The naive static rule (0.77 F1) is genuinely insufficient → *some* learning helps.
- But logistic regression TIES and k-NN BEATS the PPO agent. **RL is not the source
  of performance.** The contribution is the closed-loop self-healing SYSTEM (fix #1:
  telemetry → decision → real OFPFlowMod recovery), not the PPO algorithm.
- Honest paper framing: present detection as a classification problem where a
  lightweight model suffices, and make the SYSTEM + autonomous actuation the novelty.

**Next: Fix #4** — reframe the method language accordingly (drop "learns adaptive
strategies through environmental interactions"; either justify a real sequential
MDP or call it classification-driven control), and align the abstract/intro/claims.

---

## 8. Fix #4 (reframe) + LaTeX skeleton (DONE 2026-07-01)

**New artifacts:** `paper/main.tex` (IEEEtran conference), `paper/references.bib`
(all entries UNVERIFIED, for fix #6), `paper/README.md`, `paper/figures/`.
Cannot compile on the VM (no TeX toolchain + disk full) → compile on Overleaf
(IEEEtran bundled) or any TeX Live: `pdflatex→bibtex→pdflatex→pdflatex`.

**Reframing applied (the honest story):**
- Title/abstract now sell a **closed-loop self-healing SYSTEM**; RL is a design
  choice, not the headline. Alt honest title suggested in a comment at top of tex.
- Method section states the decision is **state-free classification**, not
  sequential RL (removes "learns adaptive strategies through environmental
  interactions").
- A **Threats to Validity** section states plainly: classification-not-RL; the
  zero-throughput labeling heuristic (idle vs failed); latency is control-plane
  RTT; small single-topology scale.
- Every number is tagged `% [VERIFIED <script>]` or `% [TODO ...]` in the source.

**Verified numbers embedded:** detection 0.96 acc / 0.98 F1 (fix #2); baseline
table incl. k-NN 1.000 (fix #3); 0% sustained loss recovery (fix #1).

**Remaining before submission:** fix #7 final grammar/style pass + confirm author
list + IEEE compile check on Overleaf.

---

## 10. Fix #6 — citations verified + related-work rebuilt (DONE 2026-07-01)

**Why:** the thesis bib was fully UNVERIFIED, with internally inconsistent entries
(author/year/method mismatches) and a placeholder `\cite`/bibliography in the tex.

**What was done (every ref web-verified against the publisher/index record):**
- `paper/references.bib` rewritten: 13 entries, all with confirmed
  authors/venue/year/vol/pages/DOI. Tooling seminal refs added (PPO, Mininet
  HotNets'10, Ryu, OpenFlow 1.3 ONF, Stable-Baselines3 JMLR'21).
- **Removed 2 unverifiable (likely fabricated) thesis entries:** "Zhou et al.,
  DRL fault management for 5G, IEEE TNSM 20(2), 2023" and "Khan et al., AI-driven
  self-healing SDN survey, Computer Networks 237, 2024" — neither could be found
  in 2 searches each, and both already failed to match their in-text citations.
- **Corrected 1 mis-attributed entry:** the title "A RL Approach to Solve Service
  Restoration and Load Management…" is **Ferreira, Aoki & Lambert-Torres, IEEE
  Access 7:145978–145987, 2019** — the thesis wrongly credited "Sahu et al., IEEE
  Trans. Smart Grid".
- **Fixed a method label:** Huang et al. 2025 is **DDPG** (DDPG-LBBP), not the
  "Double DQN" the thesis claimed (noted in the bib entry).
- `paper/main.tex`: Related Work section rewritten with real `\cite{}`s + a
  positioning table (`tab:related`); tooling cites added in Decision Unit /
  Implementation; manual placeholder `thebibliography` replaced with
  `\bibliographystyle{IEEEtran}` + `\bibliography{references}`. Cross-checked:
  all 13 used keys ↔ all 13 defined keys (1:1, no dangling/unused).

---

## 11. Fix #7 — final language/style pass (DONE 2026-07-01)

**Why:** last polish before the paper is submission-ready. (The prose was already
rewritten clean during fixes #1-#6, so the original thesis grammar slips were
already gone; this pass was consistency + honesty alignment, not a rewrite.)

**Changes to `paper/main.tex`:**
- Abstract now leads with the verified recovery headline ($40\pm13$~ms bounded
  outage, no sustained loss) alongside the $0.96$/$0.98$ detection numbers; 181
  words (within 150-250). Intro gained a one-sentence results preview that also
  states the honest "simple classifiers match the agent" framing up front.
- Throughput equation: units standardized to kbit/s and symbols ($B$,
  $T_{\text{interval}}$) defined; sentence flow fixed.
- Conclusion future-work list no longer lists "quantitative recovery-time
  measurement" (that is now DONE in fix #5) — replaced with multi-fault/multi-hop
  topologies, richer traffic, larger less-separable datasets.
- Stale header comment updated (fixes #1-#6 complete; only author list is a
  placeholder). Provenance `% [VERIFIED ...]` comments kept (they don't render).

**Consistency checks passed:** no `kbps/Kbps` mismatch, no leftover "Agent 1/2",
no "100% accuracy"/"learns adaptive strategies" overclaims; every `\ref` has a
matching `\label`; all 13 `\cite` keys resolve (fix #6).

**Author list:** user chose to keep the anonymized `Group B1` / United
International University block for now (real names / blind-review can be filled in
later in the `\author{}` block).

### The ONE remaining external step (cannot be done on this VM)
Compile-check on Overleaf: upload `paper/main.tex`, `paper/references.bib`, and
`paper/figures/`; set IEEEtran; run `pdflatex -> bibtex -> pdflatex -> pdflatex`.
Confirm the wide related-work `table*` fits and no `\cite`/`\ref` come out as `?`.
No TeX toolchain on the VM (and disk 100% full), so this must be run elsewhere.

### 🎉 All 7 roadmap fixes are complete.

---

## 12. Post-review upgrades (peer-reviewer pass, 2026-07-01)

A `/peer-reviewer` pass on the revised paper confirmed **all original integrity
issues are resolved** (100%-accuracy, decision-only reroute, leaky eval, control-
RTT-as-latency, unverified citations). Remaining items are scope/framing, tracked
here as M1-M5.

| # | Reviewer point | Status |
|---|----------------|--------|
| **M1** | No networking baseline for the recovery mechanism | **✅ DONE + verified — see below** |
| M2 | Headline 40 ms is the event-driven controller, not the PPO path; report both | **✅ DONE + verified — see below** |
| M3 | Title says "RL" but method is classification; retitle or add sequential formulation | **✅ DONE (2026-07-01)** |
| M4 | Scale: 133 rows, single topology, single fault type | TODO (needs 2nd topology / bigger data) |
| M5 | If RL doesn't help, demote to ablation or show a flapping-link scenario | **✅ DONE + verified — see below** |

### M1 — OpenFlow fast-failover baseline (DONE + verified 2026-07-01)
**New artifact:** `controller/failover_agent.py` (proactive `OFPGT_FF` groups at s1
and s3). `topology/measure_recovery.py` gained a `fault_mode` 3rd arg (`link`|`node`).
**New results doc:** `docs/recovery_baseline_comparison.txt`. `paper/main.tex` gained
§Recovery-mechanism-vs-OpenFlow-baseline + `tab:recovery` + a contribution bullet +
abstract sentence.

| Mechanism | Node fault (s2 down) | Single mid-link (s2-s3) |
|-----------|----------------------|--------------------------|
| Fast-failover (`OFPGT_FF`) | 52±10 ms (recovers) | **no recovery** (2140 ms full outage) |
| Controller reroute (ours)  | 40±13 ms (recovers) | 40±13 ms (recovers) |

**Honest finding:** for locally-observable faults both recover in ~40-50 ms and are
indistinguishable (co-located controller → fast-failover's no-round-trip edge
doesn't show; we do NOT claim the controller is faster). The decisive win: only the
global-view controller recovers a mid-path link failure that per-switch fast-
failover structurally cannot. This benchmarks + justifies the controller.
Reproduce: see the results doc header.

### M2 — honest recovery-time attribution (DONE + verified 2026-07-01)
**New results doc:** `docs/recovery_controller_variants.txt`. `paper/main.tex`
gained `tab:variants` in §End-to-end-recovery; abstract + intro reworded so the
40 ms is attributed to the EVENT-DRIVEN controller, not the RL path.

| Controller variant | Recovery time | Bounded by |
|--------------------|---------------|------------|
| Event-driven (reroute_agent.py) | 40±13 ms | OF port-status event → OFPFlowMod |
| PPO-driven (self_healing_agent.py) | **3.4±0.6 s** | 3 s telemetry poll (POLL_SECONDS=3) |

**Finding:** the PPO decision path recovers ~85× slower than the event-driven path
because detection waits for a zero-throughput reading on the 3 s `OFPPortStats`
poll (actuation itself is instant). The 40 ms headline is the event-driven
controller; the paper now says so explicitly. This strengthens the honest story
(PPO adds latency without the accuracy edge it also lacks) and directly feeds M5
(demote PPO to an ablation) and M3 (retitle away from "using RL").
Reproduce: see the results-doc header.

### M3 — honest retitle (DONE 2026-07-01)
Title changed from *"Self-Healing SDN using Reinforcement Learning: A Closed-Loop
Approach to Autonomous Fault Recovery"* to **"A Closed-Loop Self-Healing SDN
Controller: Autonomous Flow-Level Recovery with Telemetry-Driven Fault Detection"**
(the honest alt that had been sitting in the `main.tex` header comment). RL/PPO is
retained as a component + ablation, not the headline. Keywords updated (added fault
detection, flow rerouting, network telemetry). Verified no RL-as-headline phrasing
remains in the body; framing is consistent title-to-conclusion.

### M5 — PPO demoted to a (negative) ablation (DONE + verified 2026-07-01)
Two changes: (1) **fixed the action space** `Discrete(3)`→`Discrete(2)` in
`train_agent.py` + `evaluate_agent.py` (the 3rd "Block User" action was a phantom:
undefined, never used, flagged by the reviewer). Re-ran all three RL scripts; the
deployed model (`sdn_recovery_agent.zip`) retrained and its sanity check still
reroutes on link-down. (2) Added `\subsection{Ablation...}` (`sec:ablation`) to
`paper/main.tex` framing PPO as a component we test, not the headline.

**Re-verified numbers (Discrete(2), 5 seeds) — the RL agent now underperforms on
BOTH axes:**
- Detection: PPO F1 **0.962** vs logistic regression **0.975** vs k-NN **1.000**
  (PPO now *below* both simple baselines, not just tied). `evaluate_agent.py`,
  `compare_methods.py`, `docs/*` + figures regenerated.
- Latency: PPO-driven recovery 3.4±0.6 s vs event-driven 40±13 ms (M2).
- Honest conclusion in the paper: lightweight classifier + event-driven actuation
  is the preferable deployable config; RL adds ~2 orders of magnitude latency with
  no accuracy benefit. Flapping-link/hysteresis scenario named as future work (not
  run — would be the one place statefulness could help).
Paper numbers propagated everywhere (abstract 0.94/0.96, `tab:detect`,
`tab:baselines`, intro, §V-B prose). Abstract 204 words; all refs resolve.

**Remaining: M4 only** (2nd topology / bigger, less-separable dataset — the heaviest
lift). After M4, re-run `/peer-reviewer` for a final verdict.

---

## 9. Fix #5 — real data-plane latency + recovery time (DONE + verified 2026-07-01)

**Why:** peer review flagged that the paper's "delay" state feature is the Ryu
request->reply RTT (a control-plane quantity), not end-to-end data-plane latency,
and that there was no quantitative recovery-time number.

**New/updated artifacts:** `topology/measure_recovery.py` (now uses `TCLink` with
injected per-link delays so latency is a REAL, non-zero data-plane measurement and
the primary vs. backup paths differ by one hop), `docs/recovery_latency_results.txt`.
`paper/main.tex` §Evaluation + §Threats updated with the verified numbers.

**Topology (measurement):** `h1 -1ms- s1 -5ms- s2 -5ms- s3 -1ms- h3` primary;
`h1 -1ms- s1 -5ms- s3 -1ms- h3` backup. Recovery = transient outage after failing
`s2-s3`, `(tx-rx)*interval` over N cycles (20 ms probe spacing).

**Verified (event-driven `reroute_agent.py`, 5 cycles):**
| metric | value |
|--------|-------|
| recovery time | **40 ± 13 ms** (20 ms resolution) |
| primary-path RTT | 31.1 ms (jitter 3.5 ms) |
| backup-path RTT | 18.1 ms (jitter 1.4 ms) |

Reproduce: start `reroute_agent.py`, then `sudo python3 topology/measure_recovery.py 5 2.0`.
Honest caveat (in results doc + paper): injected delays are a controlled stand-in
for real propagation on a single small emulated topology; the point proven is the
metric correction (data-plane RTT, not control RTT) + a quantitative recovery bound.

**Next: Fix #6** — verify/format citations, fill the `\cite{placeholder}`s in
`paper/main.tex`, rebuild the related-work table, produce a clean `references.bib`.
