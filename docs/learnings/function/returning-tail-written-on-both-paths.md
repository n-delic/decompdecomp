---
title: returning tail written on both paths
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 160-instruction N64 timer update,
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The target had `beqz` with `li $a0,0x96` in
the delay slot, a second `li $a0,0x96`, a store, then one `jal` with a `nop` slot.

The source change, and the measured result before and after (scores, the judge used): the draft's
`if (phase != 0) { state = 1; } play(0x96); timer = 0; self->mode = 3; return;` (460) became
`if (phase == 0) { play(0x96); timer = 0; self->mode = 3; return; } state = 1; play(0x96); timer = 0; self->mode = 3;
return;` (0, the app's judge). decomp-repair `guarded_arm_tail_return`.

Failed experiments that would otherwise be repeated: the arm first (`if (phase != 0) { state = 1; <tail> } <tail>`,
100: the second `li` is deleted); both tails in an `if`/`else` (785); only the call in each arm (785, 895); a local for
the argument, `s16` or `s32` (no change); `goto` into the shared call (460); `do { } while (0)` around the store (490).

What is controlled evidence and what is one function: one function. That reorg deletes a redundant copy only in a
thread it owns, which would explain why the order matters, is a hypothesis not checked in the compiler source.
