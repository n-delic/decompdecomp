---
title: Two arms' word bytes in shared locals, their packet pointers in blocks of their own
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-26
families: arm_operand_share, arm_operand_share_block_pointer, arm_operand_share_arm_order
---

What the function (or case) looked like and the exact compiler and flags:

The SN GCC 2.8.1 model loader of docs/learnings/function/command-arm-cursor-step-first.md (618 instructions), left at
365 (315 under `-mfp32`) with only its two `gSPModifyVertex` arms differing: each builds
`c = (cmd[2] << 24) | (cmd[3] << 16) | (cmd[4] << 8) | cmd[5];` (the second at `cmd[-4]..cmd[-1]`) and stores it through a
packet pointer. The bytes came out `$v0, $v1, $a0, $a1` for the target's `$v1, $v0, $a1, $a2`, and the pointer `$a3` for
`$a0`. The `-dl -dg` table: the anonymous byte reads are block-local, so local-alloc gives them `$v0..$a1` before global.c
colours `c` and the pointer, which then take what is left.

The source change, and the measured result before and after (scores, the judge used):

Three edits, together (fast_judge under `-mfp32`, which the app's exact trial then confirmed):
- the bytes in function-level locals both arms assign (`p0 = cmd[2]; p1 = cmd[3]; p2 = cmd[4]; p3 = cmd[5];` then
  `c = (p0 << 24) | ...`): they are global pseudos then, and come out in the target's registers. All four 60 (with the
  shared function-level pointer), the first three 40; reusing other arms' variables (`r2`, `g2`) instead of fresh
  ones 130 / 110 (they conflict with the triangle arm's values);
- the packet pointer back in a block of its own in each arm (`{ Gfx *dl = gfx++; ... }`): with all four shared bytes,
  60 -> 0 (with three: 60);
- the second arm's `n = cmd[-5];` before the word, as the first arm has `n = cmd[1];` (50 -> 0 with the other two).
The function's profile was changed per function to `-mfp32` with the app's exact `compiler_experiments` trial
(`suggest`); it is recorded matched. decomp-repair `arm_operand_share` makes the exact child from the 365 saved best in
one step (`arm_operand_share_arm_order` with block pointers; its plain child 100, block pointers without the order 50).

Failed experiments that would otherwise be repeated:

- A new shared pointer for the two arms only, early or late in the block: 40 / 60, the same as the old shared one.
- `u8` byte locals: worse than `s32`/`u32` (which tie).
- Before the shared bytes, block pointers alone were 825 (the earlier entry): the pointer's block only pays once the
  bytes stop being block-local.

What is controlled evidence and what is one function:

One function. Which allocator colours which pseudo is read from this function's `-dl -dg` dumps; the claim that
global.c gives the packet pointer `$a0` once the bytes are global is a hypothesis, not checked in the compiler source.
