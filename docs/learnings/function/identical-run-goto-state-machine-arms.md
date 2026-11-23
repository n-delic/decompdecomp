---
title: identical run goto state machine arms
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 1173-instruction track-trigger state machine (an N64 game, KMC GCC 2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`): an outer
`switch` on an actor's kind (a jump table of 21), and inside it a switch on a state (ladders of up to four cases, jump
tables of 8 to 10). Two kinds' state machines (`case 2: case 14:` and `case 3: case 15:`) are near copies of each other
with a few differing calls. The saved best was an m2c draft that a search had worked on (8565 by the app's historical
score, 10770 by the current judge): a `volatile` union for a three-float delta and a state copy, `case` labels inside
`if` bodies, an uninitialised variable, a `long long` phantom local, and m2c's `goto block_N` joins.

The source change, and the measured result before and after (scores, the judge used; isolated judge, the app agreed):

A clean rewrite from the target's assembly (one `case` per jump-table target, one statement per store, a `struct { f32 x, y, z; }`
local for the delta held across a call) scored 14840 with 77 extra instructions: the same flag-update runs sit in several
arms and GCC left every copy. The target has one copy of each and the other arms branch into it. Writing the earlier copy
as `goto` into the later (a label before the later copy) removed them one run at a time: 14840 -> 12470 (the "activate"
run in the first kind), -> 7595 (the same run in the second kind, and the shared "switch off" run, with the second
kind's own copy of the tail kept local), -> 235 (the first kind's `type == 14` compare tail written as its own copy:
cross-jumping then merges it into the second kind's by itself). Library: `identical_run_goto` (decomp-repair PR 1504)
writes either copy as the goto; from the duplicated form 9925 two chained children reach 235.

Then three more edits, each by hand and now a family. A load that follows a store in the source stays behind it
(`gChased = NULL; if (m) done[m] = 1;` read `m` after the store; the target read it first): `s32 m = ...;` before the store,
235 -> 175; a global pointer dereferenced as a call argument after a store, read into a local before it, 235 -> 160;
together 100 (`load_stage_before_store`, PR 1505, chains 235 -> 160 -> 100 from the plain form). Three table-driven float
updates `F = F + (f32) tab[i] * k;` had the product in the converted value's register where the target has it in the
constant's: staging the product in ONE function-level `f32` that all three updates use fixed all six rows, 100 -> 60
(`shared_product_local`, PR 1506), and the caption call's last two rows closed it at 0. Mechanism, from local-alloc.c `combine_regs` and confirmed on toy programs: a product
in the expression is a block-local pseudo, tied to the multiply's first operand that dies there (the converted value); a
local with sets in several blocks has no quantity, so local-alloc cannot tie it and the product is tied to the other
operand, the constant. A toy that keeps the converted value alive past the multiply gives the same dest register.

Failed experiments that would otherwise be repeated:

- Four spellings of the constant's position (`k * x`, `x * k`, a product local per statement, a `p = x; p = p * k`
  step) all compile to identical instructions: cse puts the constant second (expand_binop swaps a constant to the
  back) and the tie goes to the first operand. Only a pseudo that is not block-local changes it.
- `prove_chain` with every family from the saved best (10770) found nothing in the minutes it ran: the best is not a
  program a chain can step from (case labels inside `if`s, a union scratch, an uninitialised flag), so the clean rewrite
  was the only way in. `scaffold_strip` does not see a `#define`d union view.
- Four spellings of the caption call (`var_a2 = lookupDataEntry(..)` local, nested call, block-local result, the global
  read first) leave `move $a2,$v0` ahead of the `lui/lw` of the next argument where the target has it after, 60 -> 60 (a
  grid.py hint: four spellings, identical instructions). The fifth closed both rows, 60 -> 0: the global read through a
  pointer set up before the first call, `s32 *p = &gG; v = lookup(..); caption(0, *p, v, ..);`
  (`global_argument_address_stage`, PR 1508). sched.c's order puts the result move first when the load's address is
  built in the same chain behind the call; the address pseudo born before the call shortens that chain (hypothesis from
  the `-dS` order, not read from the priorities).

What is controlled evidence and what is one function:

The match is exact (0, byte-identical, re-checked after the library merges). The goto and staging results are controlled on this one function (each family reproduces the by-hand step from the
state before it). The tie mechanism is read from the compiler source and reproduced on toy programs; the claim that the
target's source used one shared float local is a hypothesis that fits all six rows.
