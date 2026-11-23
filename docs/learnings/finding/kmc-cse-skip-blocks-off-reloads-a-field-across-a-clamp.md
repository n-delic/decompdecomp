---
title: kmc cse skip blocks off reloads a field across a clamp
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 (the function's profile carried -fno-cse-skip-blocks)
evidence: observation
date: 2026-11-23
---

A 653-instruction display-list sprite drawer (texture load, a grid of scissored texture rectangles) whose per-function profile
had `-fno-cse-skip-blocks`, the option the cse address probe adds when the target reloads `lui %hi` halves. The SDK's
`gDPLoadBlock` arguments read `spr->w` three times around a `MIN(lrs, 2047)` written as `?:` (the clamp is a short conditional
block) and `CALC_DXT` reads it again around a `x ? x : 1`. The target loads `spr->w` once (`lhu $a1; srl $a0,$a1,3; ... mult
$a1,$v0`) and tests `$a0` twice; the candidate loaded it three times.

cse's skip-blocks option is what lets a value computed before a short conditional block be reused after it. With it off every
such block ends the extended basic block, so the reads are separate. Dropping the option (the project default has it on)
removed the reloads with no source change: 750 -> 430 on the source below, 3302 -> 2742 on an older spelling, with every other
row unchanged. The probe is `kmc_cse_skip_probe`, the mirror of `kmc_cse_address_probe`: it fires when the candidate-only
rows (`>`) are loads of a (mnemonic, offset) the candidate already loaded in an earlier row.

Failed experiments that would otherwise be repeated: respelling the macro arguments (a local for `w >> 3`, `w` in a local at
the top) to make the target's single load appear under `-fno-cse-skip-blocks` only moved the reload.

Controlled evidence: the same source judged with and without the option. One function.
