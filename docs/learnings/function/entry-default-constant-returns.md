---
title: entry default constant returns
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-26
---

What the function looked like and the exact compiler and flags: a 30-instruction predicate (call a helper when a
counter matches, answer whether it did) built with KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`. m2c's form had a
result local set to 1 in the branch and a second local `var_v0_entry = 0;` nobody read.

The source change, and the measured result before and after (scores, the judge used): `var_v0 = 1;` became `return 1;`
and the final `return var_v0;` became `return 0;`, both locals gone. Exact judge: 200 before, 0 after (the saved
best was 15 in the database; the judge says 0 for it now). The library's `entry_default_constant_returns` family does
it (`prove_chain.py`: 200 -> 0). The older `flag_returns` family already covers the result that is its own default
(`v = 0; ... v = 1; return v;`, 530 -> 0 on this function); nothing covered the split-variable form (160 children, none better).

Failed experiments that would otherwise be repeated: none.

What is controlled evidence and what is one function: one function (verified 0).
