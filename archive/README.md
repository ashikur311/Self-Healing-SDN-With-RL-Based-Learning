# archive/ — retired files (kept for provenance)

These are superseded by the current system but preserved because they are the
paper's documented "before" state (referenced in `PAPER_ROADMAP.md` and the skill
notes). Nothing in the active codebase imports them.

| File | What it was | Superseded by |
|------|-------------|---------------|
| `monitor_agent.py` | Original **decision-only** Ryu app — polled telemetry, ran PPO, and only *printed* "reroute" (never installed flows). | `controller/self_healing_agent.py` (real `OFPFlowMod` actuation) + `controller/reroute_agent.py`. |
| `custom_topo.py` | Original **linear** topology `h1-s1-s2-s3-h3` (no alternate path → no reroute possible). | `topology/reroute_topo.py` (triangle with backup link). |
| `test_agent.py` | Old 3-hand-picked-scenario "test" (not a real evaluation). | `evaluate_agent.py` (held-out, ≥5 seeds) + the sanity check inside `train_agent.py`. |
| `generate_graphs.py` | Early one-off plotting script for `Telemetry_Graph.png`. | Figures now produced by `evaluate_agent.py` / `compare_methods.py`. |

To restore any of these: `git mv archive/<file> <original/path>`.
