---
title: actor motion call tail block and priority tie
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 3034-instruction N64 actor-motion handler (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`), saved best 140 with
8 differing rows after a previous agent's ~9,000 compiles. Two causes: (1) at the top of the in-motion state every
zero is a copy of one register (`move $s7,$zero`, then `sw $s7`, `move $s6,$s7`, `move $fp,$s7`, `move $s5,$s7`); the
saved best made them from a `union { u32 bits; f32 number; }` with `x & ~x` and `(bits >> 16) << 16` tricks; (2) in a
`requestSound(0xFF)` arm the target keeps `mfc1 $s6,$f26` above the call, the best had it below.

The source change, and the measured result before and after (scores, the judge used, fast_judge then compile_and_diff):

1. 140 -> 20. The store that followed the arm's last call (`rec->0xCC = 0.0f; goto join;`) moved to a block the arm
   jumps to (`goto clear; ... clear: rec->0xCC = 0.0f; join:`). `sched.c` (`schedule_block`) keeps calls and jumps that
   end a block in order at the end and makes every other insn depend on the first of them, so with the call right before
   the jump the copy (priority = the multiply's latency, 7) can no longer sink below it; reorg then fills the jump's
   delay slot with the store and threads it past (`mfc1; nop; jal; sb (slot); j join; sw (slot)`). Statement order alone
   is invariant (the copy carries `mul.s`'s latency in its priority), and a `do { } while (0)` pins it too early (its
   loop note makes the argument setup depend on it). In sched2 the copy also depends on the call (its `$f26` source),
   so sched1's order is the one that counts. decomp-repair `call_tail_store_label`.
2. 20 -> 450 -> 240 -> 0 for the zeros. A plain `f32 roll` set first (`roll = 0.0f;`) and every other zero a literal:
   cse keeps as a class leader the register whose last use is latest (`make_regs_eqv`), and the roll is read last (the
   final `pos += roll`), so every zero copies it. That exposed two allocation differences the union had hidden:
   - The case that re-zeroes the roll stored `speed_delta` (a spilled pseudo, `sw $s7,0x5c($sp)`) before
     `D_800E1A1C`; the target has them the other way. The store to the scalar global anti-depended on the loads of the
     preceding `p->0xBC->0x2C - cos(..) * 1.5f`; written as a struct member read
     (`((PosRec *) p->0xBC)->z`, `MEM_IN_STRUCT_P`), `true/anti_dependence` treats it as unable to alias a fixed scalar,
     the store's priority drops to the copy's and the LUID tie-break keeps source order (450 -> 240).
   - The two accumulators in `$s5`/`$s6` were swapped (48 rows). global.c's priority is truncated to an integer
     (`floor_log2(refs) * refs / live * 10000`): 24 refs and lives of 1769/1771 both give 542, and ties go to the lower
     allocno, i.e. the local declared first. Writing the arm's product straight into the accumulator
     (`var_s6 = fs3 * cosAngle(..)` instead of `fs3 *= ..; var_s6 = fs3;`) and `var_s6 = roll = 0.0f` moved the lives
     to 1769/1771; declaring the x accumulator at function level before the z one (it had been block-scoped) gave 0.
     decomp-repair `priority_tie_declaration_order`.

Failed experiments that would otherwise be repeated:

- Making the copy's source a pseudo that crosses no call (to use `sched_before_next_call`): sched2 then sinks it again.
- `__attribute__((const))` on the trig helpers (3795: the target reloads the angle after each call), a `volatile`
  `speed_delta` (it lands in the locals area, 0x20/0x58, not the spill slot 0x5c), and every order and chain spelling of
  the three case zeros (invariant: 420-450).
- `do { } while (0)` around one accumulator statement flips the allocation (one more weighted reference) but the loop
  note reorders a neighbour (60 at best); grids of top-of-state statement orders (2880 and 120 candidates) do not move
  the allocation.

What is controlled evidence and what is one function:

One function. The two library families reproduce their steps from the states before them (140 -> 20 from the saved
best, 240 -> 0 from the state before the declaration move). The sched.c and global.c rules are read from the compiler
source; the class-leader rule from cse.c's `make_regs_eqv`.
