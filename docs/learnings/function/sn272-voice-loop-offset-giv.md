---
title: an SN 2.7.2 voice loop's byte offset written as the index times the stride
kind: function
compiler: sn-n64-272-0001-as0004 -G0 -mips3 -O2 -mgas -meb -mhard-float
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 161-instruction N64 audio voice update loop in the 0x8008Dxxx library stretch. Its index counts from -4 to
`count - 4`; beside it, a pointer steps by 0x13C and a byte offset that starts at -0x70 (= -4 * 0x1C) steps by 0x1C and
is passed to `alSynStopVoice(&syn, base + offset)`. The default sn-n64-281 profile scored the saved best 1015. An
autopilot probe pointed at `sn-n64-272-0001-as0004 -G0 -mips3 -O2 -mgas -meb -mhard-float`, because the target loads a
double with `ldc1`, which only the 0004 assembler writes. Under that profile the saved best scored 250.

The source change, and the measured result before and after (scores, the judge used):

- `plain_best.py` (the fences opened, the scaffolding stripped): 170 under the 0004 profile. The rows left were the
  preheader order (`li $s4,-1; addiu $s0,$s1,0xaa; li $s3,-0x70` in the target), an unfilled `nop` after the guard's
  `beqz`, and the prologue's `sw $s2` scheduled first.
- The offset dropped and written as `base + i * 0x1C`: 0. loop.c's strength reduction makes the offset itself, as a
  giv of the index, after the hoisted constants. So the preheader takes the target's order, and the guard's delay
  slot stays empty.
- The app's `compiler_experiments` trial of that source under the 0004 profile was exact, and `suggest` applied it
  for this function only.

decomp-repair `counter_companion` already rewrote companions as `INIT + j * S`, but it missed this loop three ways: the
counter didn't start at 0, the steps sat in the bare blocks `plain_best` leaves, and the inits weren't the statements
right before the loop. It takes all three since 0.6.68 (decomp-repair PR #1776), and its one child on the plain form
is the exact source.

Failed experiments that would otherwise be repeated:

The same giv edit on the fenced saved best gives 120, not 0. Strip the scaffolding first (`plain_best.py`), then
apply it.

What is controlled evidence and what is one function:

One function. The edit is the only change between the plain form at 170 and the exact source.
