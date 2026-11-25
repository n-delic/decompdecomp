---
title: ray test and bit decoder rewrites
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

Two large N64 functions rewritten from the asm (SN64 GCC 2.8.1, scores under the `-mfp32` profile, app judge-worker):
a 761-instruction vertical ray test against a triangle list (m2c's `u8 sp[600]` with `#define`d slots, 23729) and a
1713-instruction animation-delta bit decoder (m2c with ~100 temporaries, 26531).

The source change, and the measured result before and after (scores, the judge used):

Ray test, 23729 -> 90:
- Named `f32` vector structs declared in frame order, with four unused vectors where the target's frame has 16-byte
  holes (an unused aggregate still gets its slot): 7109.
- The return tail: `dd = bestY - o->f44;` before the test (the target computes it first), `ret = bestY` in each path:
  bestY then outranks the hoisted double `0.0` for the last callee-saved FP register; the target spills the constant
  and rematerialises it in `$f8` at each compare: 7109 -> 1281.
- m2c's register-named temporaries (`temp_fs0`, `temp_fs0_2`; `temp_fv1_2`, `temp_fv1_3`) reused as one variable each:
  2376 -> 1616 (the library's `mips_float_local_reuse` covers this shape).
- Statement order of independent stores, found by grids: `q1.{x,y,z}` before the box stores 1241 -> 295.
- `do { } while` under a guard as a `for` (`guarded_do_while_for`): 260 -> 90.
Left: the loop head schedules both table-base sums early in the target while loop.c hoists the first symbol it meets.

Bit decoder, 26531 -> 1025:
- One cursor and one bit position, with the 4-bit, 1-bit and n-bit reads as macros: 43975 with a shared value
  temporary; written as `dst |= ((*p >> bit) & M) << sh;` in each switch case (cross-jumping merges the shared
  `sllv; or` tail and reorg fills each `j` slot with the case's `andi`, as in the target): 5175.
- Block-local temporaries in the macros (a shared one is a global pseudo that loses `$v0`): 4205.
- Sign masks `-(1 << n)` written inline (loop.c hoists them as the target does) and the zero inits chained
  (`x = y = z = 0`): 1625. The 3-bit mask masked in each arm: 1395.
- The store block and the `+=` block are the two arms of `if (frame < 2)`, not one block (m2c had both in the `if`): 1285.
- `store_run_move` (one member store moved): 1025. Left: the cursor and one value swap `$t0`/`$t1` (priorities 18356
  against 17975 in `.lreg`).

What is controlled evidence and what is one function:

Two functions, scores only; neither is matched yet. The explanations come from the dumps (`-dL`, `-dg`, `-dl`) of the
candidates.
