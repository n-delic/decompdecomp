---
title: A float-to-byte clamp whose copy sits in the branch delay slot with the value in a different register
kind: experiment
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the case looked like: a 172-instruction N64 sound-engine function (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`)
that converts four floats to bytes (`(u8)(u32)(x * k)`, unsigned conversion through the 2^31 split), clamps three of
them to 0x7F and one to 0x3FFF, and passes each to a callee. Each clamp in the target is

    andi $v1,$v1,0xff ; sltiu $v0,$v1,0x80 ; bnez $v0,L ; move $a1,$v1 (slot) ; addiu $a1,$zero,0x7f ; L: jal f

(the value in `$v1`, the argument register `$a1` a different register, the copy in the branch's delay slot, the
constant after it). Every spelling tried gives one of two outputs instead (both measured with the exact judge in a
scratch home, no permuter run):

- `beqz $v0,L ; addiu $a1,$zero,0x7f (slot) ; move $a1,$v1 ; L:` (value and argument registers right, the layout
  mirrored): the ternary `a = v >= 0x80 ? 0x7F : v`, `if (v < 0x80) a = v; else a = 0x7F;`, `a = 0x7F; if (v < 0x80)
  a = v;`, an inline `clamp(x)` helper, and the same inside a call argument. jump.c's "if (...) x = a; else x = b ->
  x = b; if (...) x = a" (jump.c:699) hoists the constant arm above the branch (it needs the else insn to be a REG,
  SUBREG or constant and the then arm a single insn); reorg then takes the hoisted `li` as the delay slot. The
  hoisted constant is also why the value and the argument land in different registers: the constant's pseudo is
  defined while the value is still live, so the two conflict (global.c `expand_preferences` only merges the
  preferences of two pseudos that do not conflict).
- `bnez $v0,L ; nop|move $a0,$s0 ; addiu $a1,$zero,0x7f ; L:` with the value in `$a1` itself (the layout right, the
  copy gone): the clamp with a call in each arm (`if (v < 0x80) f(h, v); else f(h, 0x7F);`), or `a = v; if (v >= 0x80)
  a = 0x7F;`, or any form with an identical-arm guard. Nothing keeps the value and the argument apart there, so global
  alloc gives the value the argument register through the copy preference.

What decides it (read from sched.c, local-alloc.c, global.c, reorg.c, jump.c; hypotheses where marked):

- reorg fills a delay slot from before the branch first and tries `optimize_skip` (a branch around one insn becomes
  `beql` with that insn in the slot) only when nothing precedes. The target's `move` in the slot therefore means the
  copy is before the branch in the final order, with `sltiu` after it, and the value must not be tied to the `sltiu`
  result (local-alloc ties an output to a dying input whenever the input is local; the target's value and flag are in
  `$v1` and `$v0`).
- The first scheduler puts the copy before the `sltiu` whichever way they were written, and the copy then kills the
  value's last use at the `sltiu`, which ties the two (`andi $v0,..; move $a1,$v0; sltiu $v0,$a1`, exactly what a
  hoisted copy of the value gives). Hypothesis: the value is a global pseudo in the target (live out of its block),
  which local-alloc never ties; no source shape was found that makes it one without also giving it the argument
  register.
- A guard with identical arms (`if (a >= 0x80U) { if (x) a = 0x7F; else a = 0x7F; }`) stops both the hoist and
  `optimize_skip` and gives `bnez ; nop ; li`, the layout of the target's tie-free clamp. jump.c turns the identical
  constants into store-flag arithmetic first (`neg (ltu (xor x 0) 1)`, `and 127`, `ior 127`), and the leftovers cost a
  16..88 byte unused local area (`vars=`) unless the guard folds away (a guard cse knows is true, or a float
  compare), in which case the layout fix goes too.

Measured on the function (lower is better; the saved best, a shared clamp variable with a call in each arm and an
identical-arm guard, was 449 with 21 register rows and a frame 0x48 too big):

| source | score | what is left |
| --- | --- | --- |
| per-clamp locals (value, result), ternary or if/else | 1530 | layout mirrored in three clamps, one register pair |
| the same with the guard in the first clamp | 1114 | frame too big, layout mirrored in two |
| calls in the arms, per-clamp locals | 1200 | value in the argument register in two clamps |
| calls in the arms plus the guard | 924 | the same, frame too big |

Failed and not worth repeating: do-while(0) around the default or the copy (the loop notes stop nothing here),
a flag local computed before the copy, `goto` forms, a switch, an early `cap = 0x7F` local (cse folds it into the
arm), prototypes with `u8`/`u16` parameters, `register`, `-fno-schedule-insns` (layout right, everything else wrong),
every single-flag profile change tried, and decomprepair's `prove_chain.py --families all` from each of these
(it finds only `paired_integer_lifetimes`, 1655 -> 1630).

One function; the source-level rules above are read from the compiler's source but not tested in isolation.
