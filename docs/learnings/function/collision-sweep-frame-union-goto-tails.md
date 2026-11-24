---
title: A 1337-instruction collision sweep: frame union overhang, goto-tail polarity, a real outer loop (10972 -> 5207)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 1337-instruction N64 collision sweep (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): an outer m2c `goto` loop over
actor lists, two copies of a mesh-triangle loop per index width (u16 and u8 triangle indices, the same body twice), a
plane push-out per triangle, and a closing walk over a cell list. Saved best 10972 after about 100 earlier attempts,
frame 0x1d0 against the target's 0x1c8.

The source change, and the measured result before and after (app judge through fast_judge, each step submitted):

1. 10972 -> 10589. Frame. A union of the plane buffer `union { s16 angles[3]; f32 data[6]; struct { f32 pad[4];
   f32 lower[3]; } bounds; }` is 28 bytes, and GCC 2.7.2 rounds a stack aggregate to 8, so it took 32 and every later
   slot moved 8 bytes. The target addresses `&data[4]` (its last float lands in the next local). Writing the view as
   elements of `data` fixed the frame; m2c's `spXX` spilled scalars then had to be declared in slot (hex) order,
   because reload gives spill slots in pseudo order (reload1.c `alter_reg`), i.e. declaration order. Each alone was
   worse (11651, 11427). decomp-repair `union_overhang_trim` (the combination is its first child).
2. 10589 -> 10585. `spC0[0] += r; spC0[2] += r;` in the target's order (statement swap).
3. 10585 -> 7206. m2c's `if (!(d < 0.0f)) { push; goto next; } if (second > 0) { copy; return 1; } goto next;` in both
   mesh loops written as `if (d < 0.0f) { second test } else { push }`: gcc lays out the arm the condition falls into
   first. Alone 9761 and 10075; together 7206 (the two loops' tails cross-jump with each other).
   decomp-repair `branch_polarity` now reads every `if` (it stopped at 24) and offers like-shaped goto tails together.
4. 7206 -> 7045 (-> 6975 with the cell walk restructured). The outer `loop_3:` label whose `goto`s sit deep in the
   tail became a real loop: loop.c only hoists out of a loop the C states, and the target holds the float zero in a
   saved register from before the loop (`mtc1 $zero,$f26`). decomp-repair `nested_goto_loop`.
5. 6975 -> 6790 -> 6380. The triangle-call base temporaries (`t = mesh->verts; f(t + i0 * 12, t + i1 * 12, ..)`)
   written as the field read in each argument; one of them was a phantom `long long` on a packed one-line loop body.
   decomp-repair `reload_temp_inline` (now lays a packed function out and offers 12 children).
6. 6380 -> 6360 -> 6260. m2c's `spE4 = arg0; spEC = arg2;` copies dropped (the spilled parameter's own pseudo is then
   stored at entry, as in the target), and the entry clears in the target's order (the two spilled locals, then the two
   global pointers). decomp-repair `parameter_copy_drop` (pointer parameters, functions
   with a goto) and `adjacent_init_swap` (it split a global's name into a type and a one-letter name).
7. 6260 -> 6250. The u8 loop's plane distance summed in the u16 loop's order (`((a*x + b*y) + c*z) + d`).
8. 6250 -> 5207 by `prove_chain.py --families all` with existing families from there: `fence_open`,
   `zero_guard_u16_counter` (the u8 loop's counter as `u16` with the zero start before its guard), `product_inline`,
   `adjacent_array_store_order`, `local_weight_ladder`, `guard_copy_cse_barrier`, `disjoint_local_merge`,
   `mips_float_local_reuse`. With all ~2400 children per step it stops at 5207 apart from more ladder rungs and a
   phantom wide local (5167, scaffolding, not kept).

Failed experiments that would otherwise be repeated:

- Rewriting the u8 triangle loop's two pointers as `p[0], p[1], p[2]` (7957) or as a `goto` loop like the u16 copy
  (7947, 8075): the frame loses 8 bytes of spill slots (`vars=336`), so every stack row moves.
- Respelling the u16 triangle call's arguments (casts, `* 12`, typed vertex pointers, operand order): identical code.
  Its base pseudo takes `$t0` instead of `$v1` (a local-alloc quantity), which leaves `$t1` used by a pseudo, so reload
  picks `$t2`/`$t3` as spill registers where the target has `$t1`/`$t2` (about 80 rows), and the two `copy; return 1`
  blocks become identical and cross-jump (the target's differ only in that reload register).
- The closing cell walk as the target's three `fp = fp->next` sites (6950), as nested real loops (7730, 6635): the
  cell pointer stays spilled (priority 0.075 against 0.128 for the last pseudo that got a saved register).
- `do { } while (0)` around the cell pointer's load (6780), `u16 sp166` (6415, though the target loads it with `lhu`).
- `arg3->x = sp18[0] + arg3->x` (7185); `+=` compiles the same as the plain sum.

What is controlled evidence and what is one function:

One function. The frame rule (aggregates rounded to 8 bytes, spill slots in declaration order) and the loop.c rule (no
hoisting out of a `goto` loop) are read from the compiler's source and the dumps; the rest is measured here only.
