---
title: A loop index zeroed in its declaration keeps a move, sll, addu in the preheader
kind: finding
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32 -fno-schedule-insns
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 107-instruction N64 function that picks a
table of text lines with an `if`/`else if` chain and draws them in a loop. The target set the walking pointer with
`move $v0,$zero; sll $v0,$v0,2; addu $s0,$v0,$a0`; ours had `move $s0,$a0`.

The source change, and the measured result before and after (scores, the judge used): `i = 0;` before the loop moved
into the declaration (`s32 i = 0;`), above the chain. 205 -> 0, recorded matched by the app's judge. Family:
`index_zero_in_declaration`. m2c shows the same code as `p = (0 * 4) + base;`; `m2c_zero_index_walk` writes the index
back (the machine draft went 3499 -> 2835).

Other steps on this function, from its machine draft (3145 under the old profile):
- Plain `if`/`else if` source with the three-byte colour as a struct copy: 915 under `-O2 -mfp32`.
- `-fno-schedule-insns` (the target's load order and the parameter copy in `$v1`): 715 -> 445. The app's compiler hint
  suggested `-mfp32` but not this flag.
- `f32 scroll = D; ... s32 y = scroll;` ahead of the struct copy, and the callee's 4th argument declared `f32` and
  passed `1.0f` (its `move $a2` then lands in the delay slot): 445 -> 205.

Failed experiments that would otherwise be repeated: `do { i = 0; } while (0)`, `i = 0; do {} while (0)`, identical-arm
`if (page) i = 0; else i = 0;`, `switch` with only `default`, a dead `goto` label, wrapping the loop in
`do { } while (0)`: all stayed at 205. `-O1`, `-fno-rerun-cse-after-loop`, sn-n64-272 did not make the shift.

What is controlled evidence and what is one function: one function. The reading that loop.c stops its search for the
initial value at a label is from GCC 2.7.2's source and a hypothesis for SN's closed 2.8.1.
