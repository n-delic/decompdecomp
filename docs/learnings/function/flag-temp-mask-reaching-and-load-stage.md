---
title: flag temp mask reaching and load stage
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---
What the function (or case) looked like and the exact compiler and flags: a 73-instruction N64 input handler, KMC GCC
2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O1`. The machine draft scored 45 with register rows only.

The source change, and the measured result before and after (scores, the judge used): test the mask words where they
are read (`if (bits & 2)`, `if (flag & 0xFF)`) instead of m2c's per-path temporaries, and give a global load with
arithmetic its own step (a scheduling fence, or `t = G; v = t - 1;` with a word-wide `t`): 45 -> 30 -> 15 -> 0 (app
judge). The second temporary's word is assigned several times, which `flag_temp_inline` refused until it checked the
control flow (decomp-repair 0.6.118); with it `prove_chain` goes from the draft to 0 (`flag_temp_inline`,
`entry_scheduling_scope`).

Failed experiments that would otherwise be repeated: a `u8` temporary for the load (15), `v = G; v--;` (15), a `u8`
or `s16` result of the subtraction (160 to 370).

What is controlled evidence and what is one function: one function; the library tests cover the control-flow proof.
