---
title: object initialiser stale frame slots and call result argument
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags: the 392-instruction N64 object initialiser of
`object-initialiser-switch-order-and-spilled-args.md` (KMC GCC 2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`), handed off at 197:
a frame of 0x58 where the target has 0x68 (22 stack rows), `move $a0,$s0` for memset's first argument where the target
passes the allocation's result (`move $a0,$v0`), and a flag-word update whose `andi` comes before a float conversion
(a `nop` after the `mfc1`) where the target fills the `mfc1`'s delay with it.

The source change, and the measured result before and after (scores, the judge used: `fast_judge`, the app's own):

- 197 -> 27, two edits that each move nothing alone: the float stored into the halfword field written as `(s16) f`
  (not `(s16) ((s32) f)`: the narrowing is then a copy of its own after the `fix:SI`, and the first scheduler issues
  the conversion before the flag word's `and`, so the loaded word and the conversion's value are live together and take
  `$v1`/`$v0`), and the masked word held in the loop counter that the next loop reinitialises (`i = w & 3; w = i |
  0x8010;`). A one-off temporary is block-local and set once: local-alloc ties it to the loaded word that dies in the
  `and` (`$v1`), and the first scheduler boosts it as a "birthing" insn above the conversion (sched.c
  `adjust_priority`/`birthing_insn_p`); the reused counter is global (`$a1`). decomp-repair
  `narrow_conversion_rmw_reuse`.
- 27 -> 22: memset read the field just stored, with the cursor's next step moved above the call:
  `obj->rec = p; p += 0xB8; memset((void *) obj->rec, 0, 0xB8);`. cse puts the copy of the call's result and the
  longer-lived local in one class and writes the local (cse.c `make_regs_eqv`: a register whose life reaches outside the
  block becomes the class's first); once the local has stepped, the store's class is the result's alone, and the step,
  a register operation on a saved register, is scheduled back after the call. A `do { } while (0)` around the copy
  gave the result register in cse1, but cse2 (after loop.c, which ignores the loop notes) merged it back.
  decomp-repair `stored_value_call_reread`.
- 22 -> 0: the two guarded `do { } while` loops (`i = 0; if (n > 0) do { ..; i += 1; } while (i < n);`) written as `for`
  loops. jump.c copies a `for`'s test in front of the loop; combine folds the copy with `i = 0` and, when the comparison
  pseudo's death note reaches the label before the loop, puts a `(use (reg N))` there (combine.c `distribute_notes`).
  That pseudo has no other reference: regclass gives it `ST_REGS or none`, global leaves it without a register and
  reload gives it an 8-byte slot nothing reads. One `for` was +8 bytes (score unchanged, the frame still short), both
  were the target's +16. The second loop's bound had to be `(s32) ((u32) x / 4)`: a cast helper in the bound kept the
  test at the bottom (1262), and `>> 2` let combine make a `sra` the target does not have (1020). decomp-repair
  `guarded_do_to_for` (pre-guard counter, cast-spelled bounds, helper written out, every loop at once).

How it was found: 283 earlier attempts are in the project database (`text_payloads`); compiling them all for `.frame
vars=` showed 34 with the target's 48 bytes of which 16 were never referenced; `-dl -dg` on one listed the spilled
pseudos as `ST_REGS or none`, and `-df` showed each was an `sltu` of a `for`'s copied test.

Failed experiments that would otherwise be repeated: a separate pointer for the allocation and the cursor (cursor in
`$v0`, 287); a copy of the call's result in its own local with memset reading it (cse merges it back, 197); the
allocation-failure path as `goto end` (same code, 197; kept as a cleaner shape); the flag temporary as every other local
(only the counter that the next loop reinitialises gives `$a1` without moving others); a constant local for
`sp2C = 1`, byte locals for the two `(u8)` stores (frame unchanged).

What is controlled evidence and what is one function: one function (observation). The stale-slot mechanism (combine's
`(use)` after a label for a folded `for` entry test) was read from the dumps of this function and an earlier attempt,
not from a controlled toy; `guarded_do_to_for` records the same 8-byte effect on a 417-instruction input handler.
