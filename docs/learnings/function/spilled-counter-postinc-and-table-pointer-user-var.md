---
title: spilled counter postinc and table pointer user var
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-25
---

What the function looked like: a 629-instruction N64 level loader (four loops over a record list, a path-copy arm,
an object spawner). The saved best was at 615 under the per-function fp32 profile (650 recorded under fp64). Two
groups of rows were left: twelve rows of scheduler order in a loop that calls `f(0xA9 + n++, i + base1, e->kind)` with
`n` spilled, and a `D_tab[idx] = cnt;` store in a loop arm whose table address loop.c hoisted (rematerialised through
`$t1` after reload, which swapped every later t0/t1 reload).

The source change and the measured result (dev judge, fp32):
- `f(0xA9 + n++, ..);` -> `f(0xA9 + n, ..); n++;`: 615 -> 220. Hypothesis: an argument with a side effect is
  precomputed into its own pseudo and the first scheduler put the add, increment and spilled store ahead of the other
  arguments. decomp-repair `call_arg_postinc_statement`.
- `D_tab[idx] = cnt;` -> `tp = D_tab; tp[idx] = cnt;`, with the same local also used for another read of the table
  later in the function (`tp = D_tab; r = g(0, tp[0], ..);`): 220 -> 0. Any second use outside the block worked (three
  placements tried, all 0). decomp-repair `symbol_store_pointer_share`. See
  docs/learnings/finding/loop-hoist-desirability-table-address-pairs.md for why.
- Then the exact compiler_experiments trial under -mfp32 was applied with suggest: matched.

Failed experiments: pre-increment forms (`base + ++i`, `i++` first): 965-1075; a pointer local set and used only in
the store's block (moved like the address itself): 230; `cp = &D_tab[idx]; *cp = cnt;` (shift expanded first): 220;
`cp += idx` / `cp = &cp[idx]`: 20 (result tied to the pointer's register); reusing a long-lived pointer local (`src`,
`cur`): 15 (it keeps its saved register).

Controlled evidence vs one function: both steps were measured on this function only; the loop.c reading was checked in
the -dL dump (the movables' life, savings and "moved"/"not desirable" lines).
