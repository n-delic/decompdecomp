---
title: m2c goto shared return ladder
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -O1
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 7-argument N64 function (KMC GCC 2.7.2 -O1) whose m2c draft nests (a == 0) | (b == NULL) guards, a shared label __shared_return_0: return 0 and var = f() == 0; return var tails.

The source change, and the measured result before and after (scores, the judge used):
411. The clean form if (f1 != 0) return 0; if (f2 == 0) return f3 == 0; return 0; is 0, and the guards as early returns with the tail written the other polarity are 885. Reachable by chaining the new shared_return_goto_inline and result_constant_returns (411 -> 570, an uphill step), then shared_return (570 -> 0); prove_chain's greedy chain cannot take the uphill step, the beam can. The flatten of the (x|y)==0 guards into early returns was not needed for the match.

Failed experiments that would otherwise be repeated:
Greedy prove_chain from the draft: default_into_else twice, stuck at 220 on scaffolding.

What is controlled evidence and what is one function:
One function; the chain was applied step by step by hand to confirm the final 0.
