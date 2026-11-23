---
title: audio builder halfword byte locals
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

The 616-instruction N64 audio command builder of `audio-builder-gate-record-width-scratch.md` (KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`), saved best 2555, matched (0, recorded by the app). Its blocker for two sessions was the
target's two-instruction byte form at eight sites, `andi $v1, $v0, 0x3f` then `andi $s4, $v1, 0xff` (and `andi 3` /
`andi 0xff`), which every byte, word, helper, bit-field and staging spelling folded into one `andi`.

The rule: this cc1 does not promote narrow locals. A `u8` local is a `reg/v:QI` pseudo and a `u16` one `reg/v:HI` (read in
the `.rtl` dump of a toy function). A masked value assigned to a `u8`, `s8`, `u32` or `s32` local leaves one `andi`
(combine knows the value's nonzero bits). A byte zero-extended into a **halfword** local is `(set (reg:HI v)
(zero_extend:HI (subreg:QI (and:SI x 63))))` and keeps both masks. A brute force over 144 toy combinations (byte,
halfword and word types for the masked temporary and the destination, four statement forms) kept the pair only when
the destination was `u16`/`s16` and the value went through a byte. The spelling is `u16 v; v = (u8) (x & 0x3F);`.
Why combine keeps it is not traced in combine.c.

The source change, and the measured result before and after (scores, the judge used):

Each step scored with the app's standalone judge (`fast_judge.py`), the new bests confirmed by `compile_and_diff`.

| step | score | family |
|---|---|---|
| saved best | 2555 | |
| the two byte locals `u16`, set as `(u8) (x & M)` | 3660 | `byte_mask_halfword_local` (child 1) |
| their wide copies `lo = (u8) (v & 0x1F)` uncast | 1825 | `byte_mask_halfword_local` (child 2) |
| the `int` result stored only as a halfword `s16` | 1495 | `byte_mask_halfword_local` (child 3) |
| a local read before its set in one arm (a hill climb had moved it) put back above its reads | 1270 | by hand (a correctness fix) |
| `var_s6 = 0` written in each leaf arm, not before the `if` and inside a comma gate | 1155 | by hand |
| one arm's statements in the target's store order (`grid.py`) | 635 | by hand (statement order) |
| two sums as `((x >> 5) + (v >> 5)) + 1`, as the other arms write them | 490 | by hand (operand order) |
| three statement orders in other arms (`grid.py`) | 370 | by hand |
| `(m4 * 0x10) * n` as `(m4 << 4) * n` | 305 | by hand |
| the frame counter increment written like the function's first read of the frame table | 275 | by hand |
| the command pointer as a record (union at 0x40) | 140 | `local_record_view` |
| two tail statements swapped | 80 | by hand |
| `v = (s32) (f - C) \| 0x80000000;` as `v = (s32) (f - C); v \|= 0x80000000;` (both unsigned conversions) | 60 | by hand |
| the two `m2C` member reads cached in locals read inline in the call arguments | 0 | `reload_temp_inline` (after it learnt to read a pure inline helper as its expression) |

The last step decided the remaining `$v0`/`$v1` swap at the two call sites: local-alloc ranks the `and` temporary
(2 refs over 1 insn) against the loaded word tied to its `srl` (`floor_log2(refs) * refs / life`, local-alloc.c
`qty_compare_1`, equal priorities by quantity number); the cached local added an insn inside the loaded word's life, and
without it the word ranked first and took `$v0`.

Failed experiments that would otherwise be repeated:

- Every spelling of the byte value into a byte or word local (the earlier entry's list) and, at the two call sites, six
  more casts of the mask (`(u8) x & 0x3F`, `% 64`, `(u8) (u16) (...)`, ...): identical code.
- The offset helpers (`repair_scaled_offset`, `inline_fn2`) against the plain `((x >> 6) * 0x28) + 0x100`, and every order
  of the statements before those calls: identical code (sched1 runs before allocation and normalises them).

What is controlled evidence and what is one function:

The halfword rule is controlled (toy functions, 144 combinations). Every step's before/after is this one function. The
library children reproduce the measured scores of the three family steps (2555 -> 1495, 275 -> 140, 60 -> 0).
