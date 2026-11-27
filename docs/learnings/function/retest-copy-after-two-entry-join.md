---
title: A pointer retest that only survives on a copy, behind m2c's two entries into one join
kind: function
compiler: SN GCC 2.8.1 -G0 -mips3 -O2; KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 61-instruction N64 object timer: a call returns an object or null; the work runs when the object exists or a counter
passed 300, and the part that needs the object is skipped when it is null. The target tests the call's result, then
after the counter's `slti` tests it again (`beqz $a0` over the object part). The function is built with SN GCC 2.8.1
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`; the draft was also judged under KMC GCC 2.7.2
`-G0 -mips3 -mgp32 -mfp32 -O2`.

The source change, and the measured result before and after (scores, the judge used):
The app's judge, saved best (m2c draft) 560. m2c had dropped the retest (it knows the pointer is null on that path)
and wrote two entries into one join. All of: `if (p != NULL || n >= 301) { if (copy != NULL) {..} ..} else {..}`,
`copy = p;` right after the call with only the retest reading `copy`, and the m2c delay loop as
`for (i = 0; i < 3; i++) {}`: 0. Without the copy 865 on the m2c spelling (560 as plain structured C); with the copy
and m2c's goto loop 305; the loop as `i++; while (i < 3) i++;` 400. decomp-repair `skip_body_join_retest`,
`guard_retest_copy`, `empty_counter_for` (module `guard_retest_join`, 0.6.306); `skip_body_join_retest` reaches 0 from
the saved best in one step (isolated judge).

Failed experiments that would otherwise be repeated:
`goto tail` on the null path followed by a second `if (p != NULL)`, `p == NULL && n < 301` with an early return, and a
`static inline` helper holding the retest: the first and last stayed at 560 (480 under KMC), the second 995.

What is controlled evidence and what is one function:
One function. That the copy keeps the retest because the folding passes do not know the two pseudos are equal is a
hypothesis, not read in the compiler source.
