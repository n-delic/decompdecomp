---
title: colour row pointer inline and tail pointer a0
kind: finding
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Two N64 near-misses that need the per-function `-mfp32` profile (neither exact yet, so both keep their profile).
1. The colour interpolator of `product-quotient-chain-split-two-set-weight.md` (378 instructions): three vertex colours
   stored in stack arrays, then `r = ((c1[0] - c0[0]) * u + (c2[0] - c0[0]) * w + c0[0]) * tint[0]` with
   `u8 *tint = TABLE[index];` read as `tint[0..2]`.
2. A 1585-instruction collision walk whose only difference is one register: in an arm that ends
   `func(arg0, ..); G->vy = 3000.0f; func2(arg0, 5, 0x100); return 1;` the target loads the global object pointer into
   `$a0` (`lui $v0,%hi(G); lw $a0,%lo(G)($v0); swc1 $f0,0x50($a0); move $a0,$s2`), every candidate into `$v0`.

The source change, and the measured result before and after (scores, the judge used):

Isolated app judge-worker with `-mfp32` added.
- Colour interpolator: the row read at each use, `TABLE[index][k]`, with the `tint` local gone: 2756 -> 2676 (under the
  function's own -mfp64 profile the same edit is 7616 -> 7676). No library family offered it (0 of 1459 children);
  decomp-repair `row_pointer_inline` (0.6.85) does now, and the app's proposal path lists it. A prove_chain from a
  two-move hill climb of that state reached 2540 (local_weight_ladder, which is a `do { } while (0)` around `ri = grey;`,
  then float_local_merge keeping `w` in the dead `cos1`, then arithmetic_operand_order reassociating `g`, which is not
  faithful for floats); the faithful subset is 2611.

Failed experiments that would otherwise be repeated:

- Colour interpolator: the tint read moved before the colour loads (2756, unchanged: the scheduler decides), at the top
  of the function (6466), with the colour loads in natural c0, c1, c2 order (3275).
- Tail pointer in `$a0` (all 10 under -mfp32 or worse): one `Obj *o` assigned in both arms that store through the global
  (fenced in the constant-store arm 10, unfenced 475, fenced in both 885: the other arm's store must stay after its
  argument sets, and a shared `o` then conflicts with `$a0`, global.c has no preference for `$a0` and takes `$v0`);
  `o` made to die twice (`o = G; o->vy = ..; o = arg0; f(o, ..)`: cse folds the copy back to `arg0`); the arms' other
  anonymous reads held in locals both arms assign (`ty`, `lp`, `y0`: 10; an `s32` local for the table's `y`: 205); the
  parameter renamed and reused as the pointer, its value copied to a local at entry (fenced 10, unfenced 475: cse merges
  the entry copy and the pointer stays block-local). In an isolated snippet with the same compiler none of eleven
  spellings (local, `register`, float local, argument locals, comma in each argument, raw offset store, parameter swap)
  gives `lw $a0`. Spill registers are `$t0/$f8/$f9`, so an unallocated pointer reloaded at its use would land in `$t0`.
  `compiler_experiments`: sn-n64-281 -mfp32 10, gcc-2.8.1-pm 37165, sn-n64-272-0006 -mfp32 2380.

What is controlled evidence and what is one function:

All of it is one function each. Hypotheses, not read in the compiler's source: that the row pointer's register hides the
table base from the first scheduler's alias test (so all nine stack stores wait for the first tint read); and, for the
tail pointer, that local-alloc gives a one-block, one-death pseudo the lowest free register (`$v0`, the `%hi` pseudo
dies at the load), so the target's `$a0` needs either `$v0` and `$v1` occupied over [lw, swc1) or a pseudo with a copy
preference for `$a0` (global.c `find_reg` takes a copy-preferred register over its first choice). local-alloc.c
`combine_regs` (suggestions from a copy to or from a hard register that dies) and global.c `prune_preferences`/`find_reg`
were read in the gcc-2.8.1 source.
