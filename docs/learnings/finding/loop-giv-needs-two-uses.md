---
title: loop giv needs two uses
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-25
---

What the function looked like and the exact compiler and flags: a 194-instruction N64 menu drawer, SN GCC 2.8.1
(`sn-n64-281`, `-G0 -mips3 -O2`). A five-iteration loop called a helper, then one of two helpers by a flag, then
a draw call whose last argument read a halfword table at `i`: `table[unk70].h[i]`. The target counted that halfword offset in
its own register (`addiu $s3,$s3,2` beside the `+4` pointer and the `+0x18` position), ours computed `sll $v1,$s0,1`
per iteration.

Mechanism (loop.c strength reduction): an induction variable multiple (`i * 2` for a halfword index) becomes a register of
its own only when it pays for its increment, which needs more than one use in the loop body. The draw call came after
the if/else that picked the helper, so it was one use. The game wrote the call in each arm; after cse the two arms'
tail is the same instructions and cross-jumping merges them back into one call, so the code is the same but loop.c
counted two uses.

The source change, and the measured result before and after: the draw call copied into each arm of the if/else:
2315 -> 1005 (the isolated judge and the app). Afterwards the loop's helper results are passed straight to the draw call
rather than stored in the earlier colour local (that local stops being the highest-priority callee-saved pseudo):
555 -> 475.

Failed experiments that would otherwise be repeated: pointer walks and raw offsets for the halfword access.

What is controlled evidence and what is one function: one function. The library has `join_tail_arm_split_repairs` for
the copy; this entry records why the copy matters to loop.c, so a loop with an index that is used once should try it.
