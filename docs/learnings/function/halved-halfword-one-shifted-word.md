---
title: A halved signed halfword is written from one shifted word
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A N64 function (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O1`) with two switch cases that add a halfword halved with its sign
bias to a running sum. m2c wrote each as `t2 = (u32) (x << 0x10) >> 0x1F; t = (u16) ((s32) ((s16) x + t2) >> 1);`. The compiler
folds `(u32) (x << 16) >> 31` into `srl` of the unshifted value, so the target's order (`sll 16` once, then `sra 16` for the value and
`srl 31` for the sign, both from the shifted register) came out swapped: 430.

Written from one shifted word per half, `u32 w = (u32) (x << 0x10); s32 h = (s32) w >> 16; h += w >> 31; v = (u16) (h >> 1);`,
the judge says 430 -> 0 (the first half alone: 215). The family `halved_halfword_one_word` in decomp-repair (0.6.374) proposes it;
`prove_chain.py --families halved_halfword_one_word` reaches 0 in one step.

No existing family reached it (`--families all`, 800 variants judged, no child improved on 430).

One function; the rewrite is a spelling the compiler needs, not a different program. The same compile of the copy through a local
that is read once (`v = t;`) is also exact with the copy folded or kept (both whole-function children scored 0).
