---
title: a draft one stack argument short, with the mode argument picked into a local
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
An 83-instruction N64 boot setup routine. The m2c draft passed four arguments to a five-parameter callee and picked the
fourth into a local in an `if`/`else`. The target stores 2 at `0x10($sp)` in both arms before one shared `jal`.

The source change, and the measured result before and after (scores, the judge used):
Pass 2 as the fifth argument (widening the local prototype) and write the call in each arm. App judge: 1791 saved,
1345 with the argument alone, 0 with both (`compile_and_diff`). Existing families do it: `stack_argument_tail` then
`call_into_each_arm` (or the reverse order, 616 in between) reach 0 in the isolated judge, and `prove_chain.py`
reaches 0 from the saved best through `override_store_split` and `stack_argument_tail`.

Failed experiments that would otherwise be repeated:
None.

What is controlled evidence and what is one function:
One function. No new repair family was needed.
