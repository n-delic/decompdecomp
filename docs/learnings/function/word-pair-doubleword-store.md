---
title: word pair doubleword store
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

What the function looked like and the exact compiler and flags: a 25-instruction function that calls a helper,
clears two adjacent words next to a list head, then calls a second helper with the address minus 8; KMC GCC 2.7.2
`-G0 -mips3 -mgp32 -mfp32 -O2`.

The source change, and the measured result before and after (scores, the judge used): two `M2C_FIELD(g, s32 *, 0/4) = 0;`
stores became `*(s64 *)g = 0;`. Exact judge: 210 before, 20 after; the library's `word_pair_doubleword_store` family
reaches 20 from the draft (`prove_chain.py`). The target's two `move $v0,$zero` / `move $v1,$zero` rows and the pair
of `sw` appear; four register rows remain (the pair sits in `$a2/$a3`, the target has `$v0/$v1`).

Failed experiments that would otherwise be repeated: `u64`, `long long`, `unsigned long long`, a local `s64`, a
pointer local, an `s64` array and the store moved around the calls all scored 20 or worse (1070 and 477 when the store
moved; 1500 with an `s64` global): none reached the register pair. Still open at 20.

What is controlled evidence and what is one function: one function, partial (20, not 0).
