---
title: A float value bound for a call argument and an integer store, staged in one f32 local (80 to 0)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 276-instruction N64 sound-zone update,
KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O1` (the target saves every callee register before using its argument; the
saved best was written for `-O2` and stood at 3955). A clean rewrite (struct arrays, a `switch` on the object's state)
scored 80 at `-O1`: only float registers differed in two copies of `volume * (1000.0f - d) / 1000.0f`.

The source change, and the measured result before and after (scores, the judge used): one new `f32` local assigned the
expression at both sites (`v = EXPR; f(6, id, v);` and `v = EXPR; OUT = v;`): 80 -> 0 in the app's judge. The
decomp-repair family `float_value_stage` offers it.

Failed experiments that would otherwise be repeated: the same shapes through an existing float local scored 90 and 275;
each site alone stayed at 80; `v *= ...; v /= ...;` compiled the same as the one expression.

What is controlled evidence and what is one function: one function. Why the named local gets the target's registers is a
hypothesis (one pseudo across both sites, allocated before the constants), not read in the compiler's source.
