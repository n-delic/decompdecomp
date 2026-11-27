---
title: Float square loads and branch-local call arguments
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A large N64 function's `-O1` diagnostic had loads and multiplications in a different order from the target. The
machine draft first saved two nonvolatile float fields into locals and then squared them:

```c
x = M2C_FIELD(p, f32 *, 0); y = M2C_FIELD(p, f32 *, 4);
sum = (x * x) + (y * y);
```

Writing the reads directly in the expression improved the exact diagnostic judge 9,910 -> 9,760 for either of two
independent pairs. Under the function's current `-O2` profile both forms compiled identically and scored 51,205.
`float_square_load_inline` offers each pair only when its locals and nonvolatile reads satisfy the repair's checks.

The same draft set several float argument locals in branches and made one call after the join. The target's setup
suggested the source called from each branch. The existing `call_into_arms` candidate scored 9,235 from 9,910 under
-O1; inlining two branch-specific float snapshots into those calls as well scored 8,565. The new
`branch_float_call_inline` offers the combined step with a prototype, local-use and side-effect checks. A hand
rewrite that also joined two equal tests and used direct call arguments scored 8,385; adding both direct square
pairs scored 8,345. Splitting its `||` into two identical call arms, an existing `or_arm_split` proposal, scored
8,240. The final source scored 51,180 under -O2 and became the recorded best.

These results are observations on one function, not controlled compiler tests or a match. The -O1 scores were
retained compiler trials; the function's recorded profile remained -O2. Synthetic tests exercise rewrite guards,
not the game's bytes. Removing an extra empty switch case or reversing an unrelated float test made the current
-O1 score worse; both may need to be combined with later layout corrections.
