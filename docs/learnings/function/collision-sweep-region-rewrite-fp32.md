---
title: A 1585-instruction collision sweep rewritten from the assembly, region by region (112960 -> 10)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 1585-instruction N64 collision sweep (frame 0xA0, s0-s7, fp and f20-f30 saved): one lookup of a special record,
then a loop over 0x108-byte records, each record type (`p->type == 0x12A`, `0x12B`, ... `0x11C`) a block of float range
tests against two int tables of `{x, y, z}` rows indexed by the record's `idx`, with `continue`s and early returns.
The project's profile is `sn-n64-281 ... -mfp64`; the target uses only even FP registers and saves f20-f30 with `sdc1`,
so every measurement below is under `-mfp32` (the same source scores ~10x worse under `-mfp64`: 10705 vs 1055).
The m2c draft (112960) was missing about 600 instructions (whole record-type blocks), so it was rewritten by hand from the
assembly, one record type per block, then fixed region by region with the isolated judge (`fast_judge.py --flags=-mfp32`).

The source change, and the measured result before and after (scores, the judge used):

- First rewrite with typed `Rec`/`Obj`/`Vec3i` structs, float literals for the 27 pool words (each `li.s` gets its own
  GAS literal, so repeated 100.0f/300.0f entries are expected): 4846 (`-mfp32`).
- The early lookup's pointer and the loop pointer are one variable (`p = lookup(0x12D, 0); ...; for (p = start; ...)`):
  the target keeps the start in s0 and copies it to s3 before the loop: 4716.
- A stack array's address hoisted out of the outer loop and spilled (`addiu $t0,$sp,0x10; sw $t0,0x40($sp)` in the
  preheader, 8 bytes more frame): loop.c's `move_movables` doubles `insn_count` for good each time it considers a register
  `moved_once` (an inner loop already moved it): `if (moved_once[regno]) insn_count *= 2;` is never undone. The inner
  loop's table address (hi/lo pair, two movables) was considered first and doubled the count twice, so the array address
  was "not desirable". A table pointer set before the loop (`v = TABLE; ... v[i].x`) is no movable in the inner loop,
  and the array address was hoisted: 4716 -> 3827 (with the pointer as `pa = a`), the frame became the target's.
  decomp-repair `guard_loop_table_pointer` (0.6.47) proposes it (from the final source with `TABLE[i]`, 1019 -> 10).
- Spill slots follow declaration order of the spilled locals (end, d, savedY, ret): -6.
- The second `sins`/`coss` rotation row is called cosine first in the target: `-(B) + A` for `A - B` (GCC calls in the
  order the operands are written): with the next two edits 3827 -> 2941. decomp-repair `call_operand_eval_order`
  (0.6.46) proposes it (its child alone: 3827 -> 3257).
- `arg0->vy = 0.0f;` after the computation it follows in the target; `arg0->z >= p->z` instead of `p->z <= arg0->z`
  (the operand written first is loaded first).
- A 4-case switch whose bodies the target lays out 3, 2, 1, 0 (the case after the compare tree is the first written,
  shared tails cross-jumped into the later case): 2941 -> 2410.
- Two loads the target makes before the first range test (`y = arg0->y; qy = q->y;` above `if (len <= 36100.0f)`), a field
  re-read after a call instead of the local (`q->y + 20.0`), a division split from its operand and placed after the
  statements the target computes first (`s = (a + 0x2000) & 0xFFFF; dx = ..; dz = ..; s /= 0x4000;` gives the target's
  `move $v0,$v1; bgez`), `x & 0xFFFF` instead of `(u16)x != 0` (the target loads the halfword twice), block-scoped
  temporaries for a loop's squares and for a twice-read `p->y`: 2410 -> 1055.
- The library's 1810 children of the final 10-point source (every family, `prove_chain.py --fp-flip`) all score 10 or worse.
- The call's float arguments computed in reverse order into locals (`lo = qy + T2[i].y; ty = qy + T1[i].y;`): the target
  builds the second table's address first: 1055 -> 595. `v = TABLE; v[i]` (not `v++`, not `TABLE[i]`): 595 -> 475.
- A store before a call that the first scheduler moved after the call's argument moves (`p->vy = 3000.0f;` then
  `f(arg0, 5, 0x100)`; sched1 gives the store priority 5, the hard-register argument sets 4): a `do { } while (0)` around
  the store keeps it first: 475 -> 10. Scaffolding: the 10 left is the store's pointer register (target `$a0`, ours `$v0`).

Failed experiments that would otherwise be repeated:

- For the last 10: a local pointer, a function-scope pointer shared with the other record type's store, `register`,
  `volatile` on the store or the global, `const` on the global, the stored constant as a named rodata float, a
  `const`-attributed callee, fences around the call instead: all `$v0` (or worse). Local-alloc gives a block-local
  pseudo the lowest free register, so `$a0` means the target's pointer was not block-local or `$v0`/`$v1` were busy;
  neither is found yet.
- Index form `TABLE[i]` in the inner loop (the movable pair) 1484; `v++` cursor 595; division before the float loads
  3181; `(s16)` angle test 2470.

What is controlled evidence and what is one function:

The `insn_count *= 2` rule is read in gcc-2.8.1's loop.c (`move_movables`) and seen in the `-dL` dump ("halved since
already moved not desirable" for the array address after the table pair). Everything else is one function.
