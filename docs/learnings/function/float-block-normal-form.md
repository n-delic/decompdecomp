---
title: A float clamp's m2c run at 120 matched by its normal form: unfenced, one local per value, folded
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

A 96-instruction N64 function that clips one float rectangle to another (a bounding-box clamp feeding a scissor
display-list word), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The saved best (120) was m2c's spelling with the
permuter's scaffolding: every value in a reused local (`o = load; ... o = load + o; o -= c`), `x += y` in place,
`do { } while (0)` fences and an identical-arm guard on an uninitialised local. Only register rows differed (20
register, 2 operand); the instructions were the target's.

The source change, and the measured result before and after (scores, the judge used):

The normal form of the straight-line run, all at once (app judge, `fast_judge`, then `compile_and_diff`: 0):

- fences, bare blocks and identical-arm guards removed (flow.c marks a pseudo used before it is set, and so one
  that spans the guard's blocks, as global; the guard was the permuter's way to make `oy` and the sum global);
- the offset that held two values split into one local per value (`off_a`, then `off = (load + off_a) - c`);
- `x = D; ...; x op= y;` folded into one expression at the place of the update (`a = (load + off_a) + c`);
- the value updated twice (`v = load; v += oy; ...; v += d`) split in two: `v_a = load + oy;` written just before
  `v = v_a + d;`;
- literal updates (`x -= 8.0f`) left alone; the clamps' `if`s untouched.

120 -> 0. Parts alone: fences and guards removed alone 2200; the folds on the fenced source 155; the folded run in
its natural statement order 100 (`store_run_move` and `entry_scheduling_scope` from there reached 45, with the
two-update split missing); with the second web split and the sum written where its operands are (assignment_at_use)
the judge said 0. Local-alloc reading: the natural form leaves `oy`, `ox`, the sum's temporary and the loads as
locals of one block, allocated by floor_log2(refs) * refs / live length, lowest free register first, which is the
target's order (`$f2`, `$f4`, `$f6` ... for the sum, `ox`, `oy`).

Failed experiments that would otherwise be repeated:

Grids over statement order (24 and 576 orders) and over `do { } while (0)` toggles on the m2c-spelled best moved
nothing (all 120); an identical-arm guard on any other local, on up to three loads, moved nothing or made it worse
(140-165); declaration order (500 random orders) does nothing (m2c's names do not change pseudo numbers); folding the
literal update into the sum (`x0 = (a + o) - 8.0f`) changes the schedule (975 on a natural source); moving the
`e = e1 - d` subtraction after the first clamp breaks the first scheduler's blocks (>600).

What is controlled evidence and what is one function:

One function, but the rewrite is generic and now a library family (decomp-repair `float_block_normal_form`): it fires
on the saved best itself and `prove_chain.py --families float_block_normal_form` reaches 0 in one step. The register
explanation (local-alloc order) is a hypothesis read from the dumps and local-alloc.c, not a proof.
