---
title: A probe handler: per-site webs, staged global arguments and a const global read in loops (4786 -> 60)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 459-instruction N64 handler (KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): a u8 id read from the actor, a table
lookup that either updates the actor from a held object or walks four candidates (`for n < 4`, nine live values, three
message sites that each call `sprintf` and an eleven-argument display call) and, after the loop, a timer-driven
respawn. The saved best (4786) was m2c's goto loop with an `s32` copy of the byte id used all through the loop, shared
scratch locals across the three message sites, a union and identical-arm scaffolding.

The source change, and the measured result before and after (scores, the judge used):

Exact judge, one step after another (each from the previous source):
- 4786 -> 4006: the loop written as `for` with `continue`, the byte id (`u8 id`) used directly at every site instead of
  an `s32` copy set before the loop, `if (q && cond) { ...; return; }` in place of the `goto block_N` ladder. With the
  byte used directly loop.c hoists its zero extension and the constant 2 itself (`move $s1,$s0`, `li $fp,2`), which is
  what the target shows; a copy local made the extension a loop-wide pseudo and cost the register the byte needs.
- 4006 -> 700: one local per message site for the lookup results (`a1..d1`, `a2..d2`, `a3..d3`) instead of `a b c d`
  shared by all three. One name is one pseudo: the shared ones were global pseudos with ten references and took the
  saved registers the byte id and the loop counter need (`local_web_split`, already in the library).
- 700 -> 280: `gg = D_8010969C; f(0, gg, ...)` before each display call, one `gg` for all three sites (a local per
  call stayed at 700). Library: `call_global_stage` (decomp-repair PR 1404).
- 280 -> 240: the pointer tested in front of the loop and the pointer the loop takes from a table were one local; a
  second local for the first web. Library: `local_web_split` now treats a loop body that writes before it mentions the
  name as a web of its own (PR 1404).
- 240 -> 200 -> 180: the signed byte read used as an index staged in a local, and the table's base in a local, before
  the call that indexes the table twice. Library: `call_table_stage` (PR 1404).
- 180 -> 60: the global read by the display calls declared `const`. loop.c's single-use substitution (`scan_loop`, the
  "large loop" rule) only moves a load into its one user when `invariant_p` accepts it, and a load is invariant in a
  loop with calls only when it is `RTX_UNCHANGING_P`; the substituted load is then one `(set (reg a1) (mem/u))` that
  the scheduler keeps in argument order. Two of the three sites then matched.

Failed experiments that would otherwise be repeated:
- The third display call is outside the loop. Its load stays a pseudo (`precompute_register_parameters` copies a costly
  MEM argument) and combine will not merge it into the argument move across the stack-argument stores
  (`combine.c`, `use_crosses_set_p`: any store after the load's insn blocks a MEM source, constant or not). The target
  has `lw $a1` between `move $a0,$zero` and `move $a2,$s4`; ours has `move $a2,$s4` first. Tried without effect: staged
  local at that site only (120), the buffer in a local, `do { } while (0)` around the call or the call and the sprintf
  (230, 345), around the whole post-loop `if` (775) or the whole else arm (1625), K&R prototypes, `&buf[0]`.
- `gg` alone, without `const`, scored 180: the staged load goes first in the block and `move $a0,$zero` after it.
- Removing the byte-id copy alone from the saved best made it worse (5846): the steps only pay off together.

What is controlled evidence and what is one function:

One function. The library steps are tested as rewrites with unit tests and were each reproduced by calling the new
family's `variants` on the previous step's source and judging the child. `const` on the global is an assumption about
the original declaration (a read-only object); it is not shown by anything but the instruction order.
