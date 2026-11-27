---
title: An accumulator added in both arms is one add after the join; a narrow accumulator and a wide counter keep two zero inits
kind: function
compiler: gcc-2.8.1-pm-sn-as -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

What the function looked like and the exact compiler and flags: a 29-instruction N64 table sum (SN64 GCC 2.8.1 profile
`gcc-2.8.1-pm-sn-as -G0 -mips3 -mgp32 -mfp32 -O1`): a byte counter loop over `arg0` entries, a mode byte read once
before the loop and tested `== 4 || == 6` each pass, a byte from one of two tables added to an accumulator, and
`(sum & 0xFF) + arg1` returned. Only registers differed (sum and counter exchanged `$a3`/`$v1`), saved best 30 with
three `do { } while (0)` loop-weight fences.

The source change, and the measured result:
1. Fences stripped and the arms' `sum += A[i];` / `sum += B[i];` written as `value = A[i];` / `value = B[i];` and one
   `sum += value;` after the join: 30 -> 10 (fenced best) / 10. Reorg copies the join's add into the first arm's `j`
   delay slot, so the output still shows an add per arm. Two read-modify-writes in the loop doubled the sum's weighted
   references (10 against the hoisted mode byte's 5, global.c `floor_log2(refs) * refs / live`), so the sum outranked
   the byte; with one add it ranks below it and the target's colouring (counter, byte, sum) follows.
2. `u8` accumulator, `s32` counter (every read already `& 0xFF`), and the accumulator's `= 0` before the counter's:
   10 -> 0. With one width for both (or `u8` counter, `s32` sum) cse copies the counter's zero from the sum's register
   (`move $3,$7` where the target has `move $3,$0`); each part alone stays at 5-10.

Failed experiments that would otherwise be repeated: (earlier agents) name swaps, assignment order, `u8`/`s32` types
one at a time, a `while` form and dropping the index temporary (35-55); `sum += c ? A[i] : B[i]` expands to an add per
arm just like the if/else (no change); `gcc-2.7.2-kmc` at any level is >1100.

What is controlled evidence and what is one function: one function; the allocation explanation was read from the
`-dl -dg` dumps of the pinned cc1 (`gcc-2.8.1-pm-sn-as`). decomp-repair `arm_accumulate_join` (0.6.297) offers the join
and the join with the widths and init order as one child.
