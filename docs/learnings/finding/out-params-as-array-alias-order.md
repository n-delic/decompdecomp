---
title: a getter's out-parameters are one array, or their loads hoist above the stores that copy them out
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags: a 159-instruction actor update of an N64 game
(SN GCC 2.8.1, the per-function `-mfp32` profile) calls a matrix-apply getter with three output pointers
(`get(&m, 0, 0, 0, 0, 0, 0, &x, &y, &z)`) and copies the results into the actor (`p->x = x; p->y = y; p->z = z;`). The
target alternates `lwc1 $f0, 0x68($sp); swc1 $f0, 4($s1); lwc1 $f0, 0x6c($sp); swc1 ...`; three scalar locals give
the three loads first and then the three stores.

The source change, and the measured result before and after (scores, the judge used): the outputs as one array,
`f32 pos[3];` passed as `&pos[0], &pos[1], &pos[2]` and read as `pos[i]`: 275 -> 0 in the project judge (with the
function's `void` return already right). alias.c's rule that a scalar at a fixed address cannot alias an in-struct
reference through a varying address (`MEM_IN_STRUCT_P`) is what lets the scheduler move the scalar loads above the stores
to `p->x`; array elements are in-struct, so the stores stay barriers. decomp-repair `stack_run_array` (out-parameter
runs, PR #1683) offers the array for m2c's `f32 sp68; f32 sp6C; f32 sp70;` run when every element's address is a
consecutive argument of one call.

Failed experiments that would otherwise be repeated: reordering the three copies or the declarations changes nothing
(the loads are hoisted whatever the order). An `s32` return with no `return` statement leaves one `nop` (v0 live at the
exit keeps reorg from filling a delay slot with `lui $v0`): `void` was right.

What is controlled evidence and what is one function: one function, exact after the change. In the same game a
nine-pointer getter's outputs matched as scalars (its copies are arithmetic, not stores through a pointer), so the
array is a judged alternative, not a rule.
