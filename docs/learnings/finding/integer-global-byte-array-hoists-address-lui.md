---
title: integer global byte array hoists address lui
kind: finding
compiler: sn-n64-281 (GCC 2.8.1-based) -G8 -mips2 -O2
evidence: controlled
date: 2026-11-24
---

A 44-instruction N64 function (a field load plus an integer global inside a `coss(...)` argument) sat at 60 with one
differing row: the target issues `lui $v0, %hi(G)` before the prologue's `sw $ra`, two instructions above its
`lw $v0, %lo(G)($v0)`; ours put the `lui` next to the `lw`, after the field load.

Source change: `extern s32 G;` read as `G` becomes `extern u8 G[];` read as `*(s32 *) G` (60 -> 0, the app's
judge-worker). `extern s32 G[];` read `G[0]` gives the same bytes. A scalar's `lui` is scheduled with its load; an
array's address is an ordinary address constant the compiler materialises on its own, so the scheduler places it
early (Hypothesis: read off one target, not from the compiler's source). decomp-repair `int_global_array_read`
(0.5.1016) offers both, gated on the target's `lui` (not `$at`) sitting two or more instructions above the first
`%lo` use. Its float sibling is `float_global_array_read`.

Failed experiments: `extern s32 G;` with `*(s32 *)&G`, `&G`, a pointer local, `volatile`, swapped sum operands all
stayed at 60; reading the global into a local before the field load scored 380-430 (`$v0`/`$v1` swapped);
`prove_chain --families all` found nothing before the family existed.

Evidence: controlled for this one function (the family reproduces the match from the score-60 start); no second
function was tried.
