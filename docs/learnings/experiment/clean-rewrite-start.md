---
title: a clean rewrite of a scaffolded saved best is rarely a better start for a greedy chain than the best itself
kind: experiment
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (an N64 game's own profiles)
evidence: observation
date: 2026-11-23
---

The question: agents nearly always threw away a function's saved best (m2c output that a search had dressed in gotos,
`inline_fn*` helpers, `__repair_*` views, literals and phase copies, `do { } while (0)` fences) and wrote a clean
rewrite, and the families then did the last steps. Would a library-made clean rewrite of the saved best be a start
from which greedy chains get further? decomprepair `clean_rewrite` (0.5.812, with the ordering fix in 0.5.814) writes three
cumulative levels, each a faithful program (the library's tests run each rewrite on the host and compare its output):

- `structure`: m2c's gotos as `do`/`while` (a label whose only goto jumps back at the end of the block), `if (!c) { }`
  (a forward skip in one block), `break` (a goto to the label right after its loop or switch), a duplicated
  `return` (a goto to a label that only returns), `while (c)` for `if (c) { do { } while (c); }`, dead labels gone;
- `scaffold`: also one-`return` inline helpers inlined, a `__repair_` local set once to a literal written as the literal,
  `__repair_phase_` names shortened, m2c's `*((T *) (((char *) p) + N))` spelled `*(T *)((char *)p + N)`;
- `clean`: also `((__repair_field_N *) p)->value` written as a plain deref, fences opened, identical arms collapsed,
  `scaffold_strip.strip`.

Measured with the exact isolated judge (`decomp judge-worker` through fast_judge.py, attach-only, nothing saved, scratch
`HOME`, no permuter). Set: 22 functions of the game matched in the last two days, at most 220 instructions, whose
pre-match saved best (the lowest-scoring attempt before the matching agent's session began) carries m2c scaffolding,
and the 9 open functions of at most 560 instructions with a scaffolded best. The chain is `prove_chain.py --families all
--steps 6 --children 100` from the saved best and from each clean level that differs from it.

| function | saved best | `scaffold_strip.plain` | clean levels (start) | chain from best | chain from each level |
|---|---|---|---|---|---|
| 141-insn, matched | 660 | same | — | 660 | — |
| 110-insn, matched | 379 | 499 | scaffold 379, cr 499 | 0 | scaffold 60, cr 60 |
| 184-insn, matched | 455 | 660 | scaffold 345, cr 550 | 0 | scaffold 0, cr 0 |
| 126-insn, matched | 105 | 345 | cr 345 | 0 | cr 0 |
| 185-insn, matched | 310 | 530 | cr 530 | 0 | cr 0 |
| 136-insn, matched | 165 | 375 | cr 375 | 0 | cr 0 |
| 131-insn, matched | 1205 | 1230 | structure 1205, cr 1865 | 1070 | structure 1070, cr 1655 |
| 145-insn, matched | 350 | 1245 | structure 350, cr 1245 | 260 | structure 260, cr 270 |
| 179-insn, matched | 805 | 1320 | scaffold 805, cr 1320 | 455 | scaffold 455, cr 965 |
| 94-insn, matched | 550 | 590 | cr 590 | 550 | cr 550 |
| 157-insn, matched | 368 | 1279 | scaffold 368, cr 1279 | 368 | scaffold 368, cr 225 |
| 46-insn, matched | 360 | 370 | cr 370 | 270 | cr 270 |
| 188-insn, matched | 1150 | 1155 | structure 1150, cr 1165 | 970 | structure 1015, cr 1135 |
| 170-insn, matched | 1532 | 1107 | structure 1532, scaffold 1532, cr 1107 | 702 | structure 702, scaffold 702, cr 692 |
| 176-insn, matched | 180 | same | cr 595 | 180 | cr 595 |
| 86-insn, matched | 115 | 190 | cr 190 | 0 | cr 0 |
| 138-insn, matched | 150 | 325 | scaffold 335, cr 510 | 70 | scaffold 115, cr 115 |
| 62-insn, matched | 105 | same | scaffold 105 | 105 | scaffold 105 |
| 145-insn, matched | 380 | same | scaffold 545 | 0 | scaffold 25 |
| 181-insn, matched | 435 | 719 | structure 2730, cr 3024 | 415 | structure 815, cr 850 |
| 143-insn, matched | 265 | 750 | structure 265, scaffold 695, cr 1100 | 0 | structure 0, scaffold 575, cr 815 |
| 154-insn, matched | 655 | 670 | scaffold 715, cr 730 | 330 | scaffold 390, cr 390 |
| 392-insn, open | 6131 | 6141 | structure 6131, scaffold 6146, cr 6156 | crash | structure —, scaffold —, cr — |
| 315-insn, open | 19139 | 20945 | structure 24340, scaffold 24670, cr 25764 | 19059 | structure 19059, scaffold 19427, cr 21521 |
| 369-insn, open | 11901 | 12175 | scaffold 16196, cr 18427 | 11901 | scaffold 14956, cr 15536 |
| 556-insn, open | 14156 | 16236 | scaffold 14156, cr 16236 | 12980 | scaffold 12980, cr 15086 |
| 416-insn, open | 9943 | 10728 | scaffold 9943, cr 10728 | 9513 | scaffold 9513, cr 10108 |
| 222-insn, open | 14878 | same | scaffold 14878 | 14578 | scaffold 14578 |
| 534-insn, open | 15981 | 16857 | scaffold 15981, cr 16857 | 15881 | scaffold 15881, cr — |
| 443-insn, open | 6171 | 7091 | cr 7091 | 5110 | cr 5375 |
| 172-insn, open | 449 | 1160 | scaffold 449, cr 1160 | 449 | scaffold 449, cr 925 |

Summary over the 30 functions with a clean level:
- **A clean level scores below the saved best in 2 of 30** (184 instructions: 455 -> 345 at `scaffold`; 170
  instructions: 1532 -> 1107 at `clean`). `structure` and `scaffold` keep the best's score in most functions (gcc builds
  the same graph from a structured loop as from m2c's goto loop more often than not); `clean` (offered in 27) is worse in 26,
  like `scaffold_strip.plain` (worse in 25 of 26).
- **The chain from the best clean level against the chain from the best, 29 comparable functions: 2 better, 18 the same,
  9 worse.** The two wins: a 157-instruction function where the chain from the best stalls at 368 and the chain from
  `clean` (1279) reaches 225, and a 170-instruction one (702 against 692). Losses include two matched functions whose chain
  from the best reaches 0 and from every clean level stops at 25 and 60.
- On the open functions (the ones that matter: no agent has matched them), no clean level gets a chain further than the
  best: ties at the neutral levels, worse from `clean` (19059 against 21521, 11901 against 14956, 5110 against 5375).

Why it does not do what the agents' rewrites do:
- The saved best is tuned. Its spelling is what the search kept for the score: the field views are load-bearing
  (a struct member and a plain deref are different alias sets: one 176-instruction function goes 180 -> 595 when only the
  `__repair_field_0` view is written as `*(int *)p`), fences carry loop notes, and 106 of the 221 matches of the last
  two days still contain `goto`s (m2c's goto loops are often the original's shape, or what gcc 2.7.2's loop optimizer needs
  not to run). Structuring them changes codegen in the functions where gcc's loop notes kick in (435 -> 2730 at the start for one
  search loop written as `do`/`while` with `break`).
- The agents' rewrites are not mechanical cleanups: they retype locals and records, merge m2c's temporary webs into one
  local per value and pick loop shapes, each judged as they go. None of that is provable from the source alone.
- The matched set is biased toward the best: the families that end those chains were written from these very matches
  (the match-function skill's step 5), and they key on the shape of the saved best.

Shipped anyway, as a readable start, not a permuter win: plain_best.py writes the three levels beside the saved best
when a function is claimed (`/tmp/dd-HOLDER/start/clean_rewrite*.c`), and the draft pass, beam and Deep Permuter local
lane offer them as children (REPAIR, order 5000, a level that one older family already offers is left to that family).
A prove_chain run on one open 392-instruction function stops with a library error in `narrow_return_compare`
(`int('0x5EU', 0)`), from every start, unrelated to this module.

Not tried, and what would be next: typed records from the shared header instead of byte offsets, m2c's temporary webs
merged by a reaching-definitions pass (one local per value), `MULT_HI` written as `/`; each would need its own
measurement on the open functions, since a neutral rewrite is what the chain can still step from.
