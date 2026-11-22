---
title: a search loop's saved-register order, decided by a hoisted constant's priority
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function looked like and the exact compiler and flags: a 124-instruction N64 function (KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`) that walks a table of record lists, each list ended by a 0xFFFF id: an inner
do-while over the records compares the record's id with a wanted id, decrements a count, sets a flag; the outer
counter steps the table. The saved best (60) read every instruction right; the rows that differed were the order of
`addiu $s0,$s1,2` against the hoisted `ori $s5,$zero,0xFFFF`, then (once that was fixed) `$s5`/`$s6` swapped.

The source change, and the measured result (isolated judge, `fast_judge`, then `compile_and_diff`: matched 0):

| step | what | score |
|---|---|---|
| start | saved best: second pointer `var_s0 = var_s1 + 2` stepped `+= 0x1C` beside the record pointer, an `if (g) {s0 = ..} else {s0 = ..}` before the loop | 60 |
| fold | no `var_s0`: uses read `(var_s1 + 2)`, the loop pass emits the derived address after the hoisted constant (`lockstep_pointer_fold`) | 20 (only `$s5`/`$s6` left) |
| wrap | the inner do-while in `do { } while (0);` (`hoisted_constant_loop_scope`) | 50 (`$s3`/`$s4` swapped now) |
| bump | the outer counter's `var_s3 += 1; var_s2 += 3;` in a scope too | 60 (registers right, the add sits before the `lui` of the global the guard reads) |
| load | the global read into a local first, then the bump: with `count` 15 (`$a0` for the load), with `var_v0` (the return value's local, which prefers `$v0`) 0 | 0 |

The wrap, the bump and the load are one child of `hoisted_constant_loop_scope`; the chain from the saved best
(`lockstep_pointer_fold` then `hoisted_constant_loop_scope`) goes 60 -> 20 -> 0 and the library's other families found no child
below 60 before them.

What decided it, read in the GCC 2.7.2 source (local-alloc.c `update_equiv_regs`, flow.c) and seen in the
`-dl -dg` tables (`passes.py`): a pseudo set once to a constant carries a REG_EQUIV note and local-alloc doubles
its live length, so the hoisted 0xFFFF (3 refs, 21 live, counted 42) ranked below the wanted-id local (3 refs, 37 live)
and took the later saved register, the opposite of the target. flow.c counts a reference once per loop depth, so
`do { loop } while (0)` raised the constant's refs (its set and its test) above the id's, 5 / 42 against 4 / 37: 
the constant first. The extra weight also lifted the flag set in the loop above the outer counter (6 refs / 28 live
against 7 / 39), so the counter needed weight of its own. A `do { } while (0)` is a boundary in the first scheduler:
the bump in its scope stays before the global's load unless the load is a statement before the scope.

Failed experiments that would otherwise be repeated: identical-arm `if (g) {..} else {..}` scaffolds in a dozen places
(they lengthen the id's live range by up to four insns, which is what made the saved best's registers right, but do
nothing once the pointer is a derived address); reordering the preheader statements (grid of 24); the id as the
parameter itself, as `u16`, or copied twice (a frame and prologue change, 740 to 947); duplicating the `return 1`
tails (520); `count` or another local as the load's temporary (15 to 40: the load lands in `$a0`).

What is controlled evidence and what is one function: one function, measured in the isolated judge and confirmed by
`compile_and_diff`; the live-length and loop-depth arithmetic is read from the sources and agrees with the tables
for this function only. The match relies on three `do { } while (0)` scopes and a load into the return value's local
(scaffolding, in the sense that a developer would not have written them).
