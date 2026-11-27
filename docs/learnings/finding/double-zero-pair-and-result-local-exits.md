---
title: double zero pair and result local exits
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 115-instruction N64 double-precision
arctangent approximation (a 14-term continued fraction), KMC GCC 2.7.2 `-O1`; saved best 7585 under an SN64 profile.

Draft: `mtc1 $zero,$f0; mtc1 $zero,$f1` is the double 0.0. `m2c_fp64.pair_free` did not see the pair (both halves
come from one register), renamed `$f1` away, and every comparison with 0.0 read an unset register (`unset_f1`,
`GLUE_F64(...)`). `GLUE_F64(0U, 0U)` for the remaining read compiles to words read back from the stack. With both fixed,
the same draft went from 4550 to 1505 under `-O1` (5975 for the old saved draft).

The source change, and the measured result before and after (scores, the judge used): 1505 -> 425
(`negated_guard_unnest`) -> 65 (`return_local_inline_flip`: result-local exits returned directly and the last two
exits exchanged) -> 20 (`float_local_merge`) -> 0 (`default_into_then`, which needed `negate` to read `!(a <= b)`).
Judged with the app's isolated judge; the match itself was a typed rewrite (0 after 14 judged variants).

Failed experiments that would otherwise be repeated: the exits exchanged without the direct returns scored 770, the
direct returns without the exchange stayed at 425; the SN64 profiles scored 4570 at best for the typed rewrite and KMC
`-O2` 2660 (the loop's `2 * i + 1` is strength-reduced there).

What is controlled evidence and what is one function: the pair detection is covered by controlled tests; the chain of
repairs is one function.
