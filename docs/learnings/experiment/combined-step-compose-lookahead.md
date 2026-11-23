---
title: a combined-step search (compose pairs/triples ranked by differing rows, plus a one-step lookahead) finds no combination the library does not already offer
kind: experiment
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (an N64 game's own profiles)
evidence: observation
date: 2026-11-23
---

What was tried: prove_chain's stalled step (`--combine K`) now also (1) ranks the children that did not improve by how
few of the judge's rows still differ (Judge.rows), then score, at most two per family (decomprepair `compose.rank`),
(2) merges the best K of them in pairs and triples of different families whose token edits are independent
(`compose.compositions`), and (3) judges the children of the best three of them (`--lookahead`: a second step that
exists only after a first that is no better alone). A combined step is taken when it beats the score, or ties it with
fewer differing rows.

The sample: functions of one N64 game matched in the last day whose saved best before the match differs from the
matched C in 2-4 separate token regions, where each region alone compiles and does not improve and all of them
together are exact (7 of 43 such before-states). Each was run from that before-state with the isolated judge
(`decomp judge-worker`, attach-only; no claim, no permuter), `--families all`, 8 steps, 80 children per step.

| before-state | regions | greedy (`--combine 0`) | old merges (`--combine 10`) | compose + lookahead (`--combine 8 --lookahead 3`) |
|---|---|---|---|---|
| a 214-instruction function (70) | 2 | 0 | 0 | 0 |
| a 68-instruction function (20) | 3 | 0 | 0 | 0 |
| an 89-instruction flag ladder (90) | 3 | 0 | 0 | 0 |
| a 150-instruction function (10) | 4 | 0 | 0 | 0 |
| a 145-instruction function (350) | 3 | 260 | 260 | 260 (615 s against 250 s) |
| a 235-instruction menu routine (390) | 3 | 300 | 300 | 300 (621 s against 255 s) |
| a 115-instruction pointer walk (110) | 4 | 110 | 110 | 110 (also with 200 children and 300 merges) |

The four that match do so in the greedy chain alone: library families (several added this week) take the steps one
improving child at a time, or offer the whole combination as one child (the 150-instruction function: its two renames
of one register-named local are one `result_register_named_local` child). For the three that do not, the halves are
not offered: comparing every child of the before-state with the before-state plus each subset of the matched regions
(whitespace ignored), the pointer walk's children reproduce only its neutral identical-arm collapse; its declaration
swap with the narrowed type and its dropped `(u32)` casts come from no family. The menu routine's two regions are one
statement block moved under a later guard: a deletion and an insertion, which no merge of independent edits makes.
(The greedy and old-merge columns ran with decomprepair 0.5.734, the new search with the library's main of the same
day; the four matches are the same in both.)

So the commonest "each worse alone, exact together" blocker is not a search problem once the library offers the
halves: the halves are missing. What helps is a family for the missing edit (it then usually offers the whole
combination itself, as the four above show), not a wider search. The lookahead costs about twice the step time,
mostly in the families that run up to their 120 s cut-off on every `variants` call (`loop_carried_temp_copy`).
Shipped off by default (`--combine 0`); the search stays for agents to try (`--combine 8`).

What is controlled evidence and what is one function: the table is seven functions of one game, each run once; the
"no family offers it" checks are exact string comparisons of every child.
