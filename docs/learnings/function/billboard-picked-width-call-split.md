---
title: A picked call width split into the arms and a copy hoisted above its early return (865 -> 0)
kind: function
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2 (per-function -mfp32)
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 179-instruction N64 billboard drawer (a textured quad: four vertices, two display-list words), `sn-n64-281 -G0 -mips3
-O2 -mgas -meb -mcpu=VR4300 -mhard-float`, saved best 865. After a texture lookup it loads the texture with a width picked
by `w = 0x10; if (o->f74 == 0 && *(s32 *)(p + 8) == 0x20) { w = 0x20; } f(w, q, 0, 0);`, and m2c reused `w` much later
for the copy of a vertex-slot call's result (`idx = alloc(); if (idx == -1) { warn(); return 1; } w = idx; ...
done(idx - w, g)`).

The source change, and the measured result before and after (scores, the judge used):
- `prove_chain --families all --children 40` (decomp-repair 0.6.47): `scalar_storage_reuse` 865 -> 345,
  `local_weight_ladder` -> 260 (the app recorded 260).
- The call written in each arm with its literal (`if (C) { f(0x20, q, 0, 0); } else { f(0x10, q, 0, 0); }`): the width
  is then two hard-register sets of `$a0` (`addiu $a0,0x10` in the `bnez` slot, `move $a0,$v0` in the `beql` slot)
  instead of a twice-set pseudo kept in `$t3` and moved at the call. Alone 635 (worse): the later `w = idx` web now got
  `$a0` (block-local, tied to the subtraction feeding the call's first argument) and shifted every vertex pointer.
- The copy `w = idx;` moved above `if (idx == -1)`: it then lives across that block's end, global.c allocates it
  (`$t3`), and reorg puts it in the `beq` delay slot as the target has it. Alone 250.
- Both: 10 under the function's `-mfp64`, 0 under `-mfp32` (the one remaining row was `$f1` for `$f2`); applied as a
  per-function profile through a `compiler_experiments` trial and `suggest`. The app recorded the match.
- decomp-repair 0.6.48 (PR 1755): `selected_argument_call_split` now takes the pick through a one-shot
  `do { } while (0)`, plain assignments between the pick and the call, and a later web of the local, and offers the
  split together with the copy hoist as one child; `guard_return_copy_hoist` is the hoist alone. `prove_chain
  --families selected_argument_call_split` from the saved 260: 0 in one step.

Failed experiments that would otherwise be repeated:
`sMax = 0x7C0; tMax = sMax;` before the width block (385), a separate local for the copy without the hoist (915), the
width as a `?:` argument (645-915), every order and spelling of the two `0x7C0` locals (280; the orders that scored 30
read `sMax` before setting it).

What is controlled evidence and what is one function:
One function, judged by the app's judge-worker and recorded by the app; the allocator readings come from
`explain_regs.py` (block-local versus global) and are hypotheses the scores agree with.
