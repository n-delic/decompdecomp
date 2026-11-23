---
title: A 673-instruction two-arm loop walker: 1494 to 0 with seven source steps
kind: function
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3
evidence: verified
date: 2026-11-23
---

A 673-instruction collision walker (KMC GCC 2.7.2, `-O2 -G0 -mips3`, an N64 game's scene-ray function): a walk over an
actor list, then over a list of rooms; each element tests a bounding box and then loops over a triangle table with a
16-bit and an 8-bit variant (four such loops). Saved best 1494 (permuter, with a volatile pad and a trailing gap
array); matched at 0 after these steps, scored with the app's judge (fast_judge, confirmed by compile_and_diff):

1. table base `*(s32 *)(rec + 0x34)` read inline at each use, not through temp locals; a third argument on two callees
   (the asm loads three); pointer locals for the hoisted local addresses declared in slot order, the one use inside a
   comma expression left as the plain array: 1494 -> 865.
2. `var_fp = arg1` copy (prologue order), the list cursor and the link pointer as one local (`disjoint_local_merge`): -> 336.
3. counter inits hoisted before their guards in the second section, guards written `len > counter` (dead compare
   pseudos give the frame its missing spill slots, see dead-guard-compares-pad-the-frame), `hit` as one f32: -> 395 /
   285 with the library's `loop_counter_reassign`.
4. the guard's compare into a local before the init (`guard_compare_first`): -> 85.
5. `p = field; p += off;` instead of `p = field + off;` in all four loops (`load_sum_split`): -> 60.
6. the second test of the `&&` pair carried by the dead call-result local (`condition_value_reuse`): -> 0.

Scaffolding the match relies on: the two volatile stores of the actor start's x and z (`projected`, the target stores
them at 0x18 and 0x20), one-f32 `hit` standing for a frame the original had, and `register` on the loop counters
carried over from the saved best.

Failed: separate locals for the u16 call result (the first scheduler then puts the `andi` after the argument reloads,
because a single-set pseudo is a birthing insn); unifying it with the second call's result (allocated `$v1` there);
moving the init into a `do { } while (0)` fence (folds the compare); declaration order and renames searched by hill
climbing (nothing under the best); the library's families from the 85 state (none improved before the two last
families existed).
