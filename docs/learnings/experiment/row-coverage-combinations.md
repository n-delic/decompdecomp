---
title: ranking single children by the target rows they fix finds no combination that ranking by score missed
kind: experiment
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (an N64 game's own profiles)
evidence: observation
date: 2026-11-22
---

draft-start-candidates.md section 4 (prove_chain `--combine`) found no combination for three functions whose match
needed three edits, each worse alone, and guessed the cause: the edits rank far below the ten best of ~250 children by
total score. This tests the obvious fix: rank by the rows of the target's difference a child fixes (the judge's rows
against the parent's; `app/row_coverage.py`), keep any child that fixes at least one row though its total is worse,
combine children that each fix rows of their own (fewest predicted differing rows first), judge the combinations.
Shipped in prove_chain.py (`--combine K`, rows on; `--no-rows` is the score ranking) and `app/pair_search.py`
(`pair_row_coverage`, off). Measured with the exact isolated judge (`decomp judge-worker`, attach-only, nothing saved,
no permuter), from the saved bests before each function's match, the family that does the whole fix in one step left out:

| function (pre-match best) | combine, by score | combine, by rows | children that fix a row |
|---|---|---|---|
| a 108-instruction byte-cursor walk (100), without `walked_table_index` | 100 (120 merges judged) | 100 (148) | 1 of 246 |
| a 174-instruction switch (30), without `arm_switch_volatile_chain` | 30 (131) | 30 (131) | 0 of 218 |
| a 113-instruction float function (25) | 25 (87) | 25 (87) | 4 of 290 |

The same on the app's pair search: 59 matched functions whose best non-matching attempt differs from the matched C by
two line hunks, 10 of them true pairs (each hunk alone compiles and does not improve). Singles (up to 150, the
library's children of the before-state) plus 120 pairs judged: by score 5 reach 0 and 6 improve; by rows 5 and 6, the
same functions and the same winning pairs (each of the 5 is a single child that already improves, or a pair the
score ranking already composed).

Why nothing changed: the edits of a three-edit match do not fix rows alone. Applying each edit of the saved match to
the before-state by itself and reading the rows: 1 of 8 edits of the cursor walk fixes a row (and breaks 37), none of
the 4 of the switch, 1 of 10 of the float function (it fixes all 5 differing rows and breaks 6 others; its children
at the next level only undo it). The values flow through shared registers and locals, so the rows move only when
every edit is present; the single steps change other rows. A "fixes A, breaks B, the other fixes B" pair needs B to
differ in the parent, and it does not: B matches until the first edit breaks it. Row coverage helps only where an
edit fixes its own rows independently, which the score ranking already finds (the pair-search winners).

What would be next, untested: a search over what each edit does to the compiler's intermediate decisions (register
allocation order, scheduling) rather than to the final rows.

Also found while rebuilding the pre-match states: decomprepair 0.5.666 `join_tail_arm_inline._fold` raises
`ValueError` (`sorted(variables, key=names.index)` with a variable not in `names`) on the cursor-walk function's
best source, which makes `draft_repairs.variants` raise for every family (the whole call, not just that family).
