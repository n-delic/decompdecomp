---
title: stack voice config store order
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
An N64 sound library's initialiser (166 instructions, KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`). Its loop fills a
three-member voice configuration on the stack (`s16`, `s16`, `s8`) and passes its address to the voice allocator. The
saved best stood at 60 after 63 attempts: one `sb $zero` of the last member sat after the `sh` of the second where the
target has it first in the block, above the load of the first member's value.

The source change, and the measured result before and after (scores, the judge used):
The last member's store moved above the other two (`cfg.pitch = 0; cfg.priority = ...; cfg.bus = 0;`): 60 -> 0, the
app's judge. The six orders of the three statements score 0 (that one), 60 (four) and 120 (one), so swapping neighbours
reaches it only through a step that scores the same. No scaffolding.

Why the permuter missed it: `store_run_order` moves a store to the front of its run but could not read `local.member`,
and `statement_slide` treats a `.` as a barrier. None of 1,898 library children improved on 60. `store_run_order` now
reads a local struct's members and tells them apart from cells read through a parameter the function never reassigns;
`prove_chain.py --families store_run_order` from the saved best reaches 0 in one step.

Failed experiments that would otherwise be repeated:
None.

What is controlled evidence and what is one function:
The six-order grid is controlled; the benefit is measured on one function.
