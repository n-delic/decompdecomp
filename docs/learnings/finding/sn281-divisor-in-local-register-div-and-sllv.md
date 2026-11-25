---
title: A divide by a register holding 3, and sllv by it: the divisor is a local
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags: a 138-instruction function built by SN GCC
2.8.1 (`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`, a per-function profile) that places four points
on a line from an object's position (a coss/sins offset scaled by `* 25 >> 11`), with random jitter on the inner two.
The target keeps 3 in `$s7` from the prologue and has:
- `div $zero, $sN, $s7` three times, each with the `bnez`/`break 7` and `-1`/`0x80000000`/`break 6` trap checks,
  where a literal `/ 3` gives the `0x55555556` multiply;
- `sllv $v1, $v1, $s7` inside both `* 25` multiplies (`(x * 3) << 3 + x`), and `beq $s3, $s7` for `i != 3`;
- `addu $v1, $fp, $v1; subu $v1, $v1, $fp` (an unfolded `(z + c) - z`) and an `addiu $s5, $s5, 0` in the loop.

The rule: the divisor is a local set to the constant (`n = 3;`). `expand` sees a pseudo, so the division is a real
`div` with its trap checks (cse later knows the pseudo is 3, but the `div` pattern needs a register). Once cse knows
that register equals 3 it also uses the register in place of the constant wherever that is no more expensive: the
shift count inside the synthesised `* 25` and the comparand of `i != 3`. The unfolded `(z + c) - z` and the `+ 0` step
are the loop pass's work: the source wrote each point as `start + ((end - start) * i) / n` with `end` computed before
the loop (`ey = y`, so the y step is 0), and strength reduction turned `(end - start) * i` into accumulators whose
increments it computed in the preheader after cse and combine had run.

The source change, and the measured result before and after (scores, the judge used): the saved best (6660, m2c's
accumulators and a literal `/ 3`) became 2780 with `n = 3` and the accumulator form, 2555 with the lerp form
`((ex - x) * i) / n + x + ox`, 1011 with `Vec3i *p` indexed as `p[i].x/.y/.z` (one pointer register, no offset givs),
270 with the stores in x, y, z order under `-mfp32`, and 0 with `i = 0;` set at the top and `for (; i < 4; i++)`.
The app's `compiler_experiments` `-mfp32` trial was EXACT and was applied with `suggest`.

Failed experiments that would otherwise be repeated: splitting `ez = z + c; dz = ez - z;` before the loop (still
folded), raw `s32 *p` with `p += 3` (loop.c made `p + 4`/`p + 8` givs), moving `n = 3` or reordering its declaration
(the `li $s7, 3` placement only moved with the `i = 0` assignment).

What is controlled evidence and what is one function: one function. The div-by-register-with-traps signature is
reliable for "the divisor was a variable"; `constant_in_local` in decomp-repair offers a constant in a local, which is
the edit (search it on division operands first when the target divides by a register that holds a constant).
