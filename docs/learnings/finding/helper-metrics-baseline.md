---
title: App helpers' matching metrics before the skill's knowledge reached them (baseline)
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (mostly)
evidence: observation
date: 2026-11-22
---

What was measured, and on what: the app's own matching helpers (helper sessions of matching tasks; polishing left
out) in the development data home, 2026-11-18 to 2026-11-22, read with `tools/helper_metrics.py` (every database
opened `?mode=ro`). Mostly one KMC GCC 2.7.2 N64 game (6,383 of 6,514 sessions), with Codex and API helpers. These
days ran the app before the changes below, so they are the baseline.

The changes being measured (merged 2026-11-22): the near-match guide's GCC 2.x pass rules and hand method, with the
brief pointing at the section a near-match's rows call for and suggesting `find_similar` (#2844); the tools
`search_learnings` (#2839), `prove_chain` (#2841), `compiler_source` (#2843) and marked grids up to 2000 in
`score_permutations` (#2842); the 45-minute hand-off clock on near-matches (#2846).

Baseline (all five days): 459.6 helper-hours, 979 matches, **2.13 matches per helper-hour**, **134 compiles per
match**, 337 near-matches compiled by helpers of which 237 reached 0 (**near-match close rate 0.70**). Per day the
rate of matches per helper-hour ranged 1.4 to 4.2 and compiles per match 27 to 355, so compare periods of several
days, not single days. The one game alone: 1.38 matches per helper-hour, 204 compiles per match, close rate 0.74.

How to read the after: once the app runs the new code (update and restart it), run

    python tools/helper_metrics.py --home ~/Decomp-RetroReversing-dev --from 2026-11-18 --to <today> --split <restart day>

and compare the "before" and "from" rows. The change helped if matches per helper-hour and the near-match close rate
rise and compiles per match falls, on a similar mix of functions (the claim order shifts toward harder functions as
the easy ones are matched, which by itself lowers all three: compare with `--project` on the same game). Tool usage
of the new tools is in the Agents view's tool counts (`agent_tool_task_usage`).

What is controlled and what is not: an observation of a live, changing workload; no control group. The metric
definitions are tested (`tests/test_helper_metrics.py`); the numbers are one home's.
