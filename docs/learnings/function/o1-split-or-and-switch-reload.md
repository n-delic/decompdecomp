---
title: o1 split or and switch reload
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 146-instruction N64 drawing function (display-list commands chosen by a record's kind),
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (the default -O2 scored 6380 for the same source).

Under -O1 plain C was 1635. The target compares a byte with 4 and then 3 on two branches, where `x == 4 || x == 3` is
folded to `addiu -3; sltiu 2`: write the arm once per test (`if (x == 4) A else if (x == 3) A else if (!y) A else B`;
jump2 merges the copies), at two of the function's sites only (415). A two-case `switch` whose target loads the byte
again before the second compare is an `if`/`else if` chain (0, the app judged it exact).

decomp-repair: `or_arm_split` now offers pairs of conditions and `switch_if_chain` is new; `prove_chain` goes
1635 -> 415 -> 0 with the two. The greedy chain with all families took `sentinel_constant_local` first and stalled at
470 before the pairs existed.

Verified for this function; one case.
