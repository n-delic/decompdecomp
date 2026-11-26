---
title: Two empty jr ra exits: a float return whose jump survives to jump2
kind: finding
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2 (per-function -mfp32)
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 42-instruction N64 range test, `sn-n64-281 -G0 -mips3 -O2 ... -mhard-float` (exact under a per-function `-mfp32`),
saved best 350 after many agents. It truncates `D->x - o->x`, tests it against one of two tables, and on a hit leaves by
`bnez -> A`; then truncates `D->z - o->z` and ends `blez -> B; A: jr $ra; nop; B: jr $ra; nop`: two empty exits that
the compiler kept apart, and `lui $v0` in the `bnez` slot (so `$v0` is dead at A).

The source change, and the measured result before and after (scores, the judge used):
`f32` return of the float difference (`f`, set twice, always in `$f0`), `if (c) return f;` for the x test, and the z
part written with its compares dead: `if (d > 0) { if (A[i].c < d) return f; } else { if (d < B[i].c) return f; }`
falling off the end (30); then `t = o->x; f = D->x - t;` so the subtrahend is loaded first (0;
`temporary_hoist` from the library finds that step from 30 by itself). The app recorded 350 -> 210 -> 40 -> 0.

Why (gcc-2.8.1 jump.c, read): `jump_optimize` turns a jump to the function's end into a RETURN ("If a jump references
the end of the function") *before* it deletes a jump to the following insn, and only after reload (`HAVE_return` needs a
null epilogue). So the target's A is a `goto end` that survived until jump2. Before reload a USE (from `return f;`,
`expand_value_return`) is an active insn, so a jump over one is not "to the following insn"; after reload USEs are
skipped and the noop `(set $f0 f)` is deleted. Every shape that left nothing between A's jump and the end before reload
lost the z test (`blez` to the next insn, deleted with its computation in jump2): `return;` paths, a value returned at
the end (`return c`, the pointer), or the same `return f` twice (jump1 moved the identical `(set $f0 f)` above the
branch, 845).

Failed experiments that would otherwise be repeated:
`s32` returns of `c`, of the pointer or nothing on either path (210-860), one `return f` per path with a second float
(545-690), the x and z differences in separate floats (30, same as one), operand-order spellings of the subtraction
(`-o->x + D->x` 620, `f = D->x; f -= o->x;` 2350).

What is controlled evidence and what is one function:
The jump.c order is read in the compiler's source and agrees with every variant above; the float-return reading of the
target (A's `$v0` dead, both differences in `$f0`) is this function's evidence. A library family for the dead-compare
tail does not exist yet.
