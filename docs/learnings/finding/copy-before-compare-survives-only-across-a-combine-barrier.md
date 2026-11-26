---
title: a copy the target keeps before a compare: cse gives the right RTL, combine folds it unless the copy's destination outlives the compare
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float (-mfp32)
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 145-instruction N64 positional-sound update (SN64 GCC 2.8.1, the function's profile plus `-mfp32`, which fixes
every float register row). Its then-arm computes a volume quotient, stores it, and clamps it against a table entry:
the target is `mflo $a0; ...; sw $a0, 0x90($s0); lw $a1, 0($a1); move $v0, $a0; slt $v0, $a1, $v0; bnel $v0, $zero;
sw $a1, 0x90($s0)`. Every spelling compiles to `slt $v0, $a1, $a0` with no `move` (the only row left under `-mfp32`).

What the dumps say (read in gcc-2.8.1 `cse.c`, `combine.c`, `flow.c`):

- cse (`make_regs_eqv`) keeps a copy's destination as the canonical register only when that destination's life
  leaves the cse block. Written `o->vol = quotient; t = o->vol; if (D[ch] < t)` with `t` also used in the else arm
  (outside the block), the `.cse` dump has exactly the target's shape: store from the quotient, `t = quotient`,
  compare `t`. Without the else-arm use, cse folds `t` away.
- combine then substitutes the quotient into the compare and deletes the copy, because `t` dies there
  (`can_combine_p` refuses only for a call between, a set of the source between, a volatile insn, or another basic
  block: LOG_LINKS exist only inside a flow block).
- With `t` live after the compare (a diagnostic read after the `if`), the `move` comes back (`move $v1, $a0`).
  But the target's `slt` writes the copy's own register, so in the target the copy dies at the compare: something
  between copy and compare stopped combine in the original. Not found: no call, label or volatile insn sits there in
  the target.

The source change, and the measured result before and after (scores, the judge used):

- Saved best 210 (110 `-mfp32`) -> 205 (105) with `flag = D[ch] < vol; if (flag) {...}` in the then-arm and
  `flag = o->unk70 * 100; o->unk64 = flag;` in the else arm (fast_judge, confirmed by `compile_and_diff`): the flag is
  a global pseudo and `slt` takes `$v0` as the target. Either half alone: 210. decomp-repair `compare_flag_arm_share`.

Failed experiments that would otherwise be repeated (all `-mfp32`, all 110 unless given):

- static inline helpers for the quotient, the clamp, or both (identical code); a min helper (220-670);
- dead initialisations of the compared local (at declaration, at entry: 110 or 935);
- the quotient's local reassigned to the table entry between the copy and the compare (110; cse renames it);
- `do { } while (0)` around the copy, the store or after the copy, every position (350: the table address moves);
- if/else stores and the ternary (365-1150); `t` held across the `if` and stored at the join (125: an extra store);
- other compilers on the saved best: SN 2.7.2 0001/0004/0006/970404, KMC 2.7.2, gcc 2.8.1-pm, `-G8`, `-mips2`
  (all worse; 0006 at 340 with the same missing `move`); flags `-O3`, `-fno-rerun-cse-after-loop`,
  `-fno-strength-reduce`, `-fforce-mem`, `-fforce-addr`, `-fno-caller-saves` (110).

What is controlled evidence and what is one function:

One function. The cse/combine reading is from the passes' source and the dumps of this function; the reason the
original's copy survived combine is still open.
