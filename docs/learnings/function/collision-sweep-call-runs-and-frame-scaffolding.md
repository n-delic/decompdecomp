---
title: collision sweep call runs and frame scaffolding
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

A 1928-instruction N64 collision sweep (KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): an outer loop over object
lists, then a loop over actors with two arms, each arm two triangle walks (halfword and byte indices), each walk a ray
test and a plane fallback with three `segmentClosestPoint`/`distanceSquared3D` pairs and a nearest-of-three choice. Frame
0x290. The saved best (22024, rechecked 24474 under the current header) was a long search's m2c source: phantom
caller-save slots, float-to-int casts in float compares, `if (0 == 0) goto` guards, a reader call above its writer.

The source change, and the measured result before and after (scores, the judge used):

Isolated judge (`fast_judge.py`), each step submitted; 24474 -> 6367, every step a decomp-repair family now:

| step | score | family |
| --- | --- | --- |
| two interleaved caller-save slot round trips dropped, copies folded | 24474 -> 23648 | `caller_save_slot_drop` (interleaved, `_all`) |
| the actor arm's polarity and its shared tail label | 23648 -> 23701 (seed) | by hand (`branch_polarity`-like) |
| `&sp168`/`&sp180` call arguments named beside their spilled siblings | 23701 -> 21398 | `loop_address_argument_local_all` |
| `&sp108`/`&sp118` per loop, `nested_and_if` | 21398 -> 19232 | same, `spilled_invariant_repairs` |
| `!(v & 0x80) && !(v & 0x20)` nested | 19232 -> 18477 | `nested_mask_tests` (was past `nested_and_if`'s first-four limit) |
| a plane copied to an out-parameter as one struct copy | 18477 -> 17437 | `frame_array_block_copy` |
| m2c's vertex-index temporaries back into the call, `* 3 * 4` folded | 17437 -> 16757 | `call_index_temps_pointer` |
| `((s32) d) < best` casts dropped and the range tests nested | 16757 -> 13774 | `float_truncation_cast_drop_nest` |
| `dist` above its `seg` swapped back, verts loads sunk to their call | 13774 -> 12863 | `call_pair_order_load_sink_all` |
| `if (0 == 0) goto` as a NULL flag test, copies as word/element stores | 12853 -> 12008 | `constant_guard_flag_stores` |
| the nearest-of-three minimum kept apart from the first distance | 12008 -> 9918 | `minimum_result_copy_all` |
| cursor rebased / element 0 read through the stepped base | 9242 -> 8544 | `cursor_offset_rebase` |
| `(i + 1) <= (u16) n` as `i < (u16) n` | 9064 -> 8884 | `compare_bias_fold` (parenthesised bound) |
| loop counter inits sunk into their guards | 8544 -> 8155 | `init_sink_into_guard` (every site; flags skipped) |
| verts loads inline in the calls' operands | 8155 -> 6616 | `call_operand_load_inline_all` |
| `__repair_store_N` temporaries back into the stores | 6616 -> 6367 | `deferred_store_undo` |

Frame: the target's `spD8` vec3 overlaps `spE0` (a union like the matched neighbour's `secondPoint`), and `spF0`/`sp178`
sit just below `spF8`/`sp180` (address-taken scalars are allocated at their first `&`, after the arrays, so they need
to be members of a struct with the array). With both, the stack rows fall from 41 to 3, but the score rises (6616 ->
6988): the remaining differences are global.c's saved-register order, which the old layout happened to suit.

Failed experiments that would otherwise be repeated:

- One `&sp108`/`&sp118` pointer local set at the actor loop's head (the target keeps them in `$s6`/`$s5`): 15519, 9147
  later; each block's own local is better. Plain `&array` everywhere (no m2c spill locals): 16777, 18405.
- `hitIndex` as a function-wide local instead of m2c's union: frame +8; declared in its arm: right slot, +100.
- `u16` prototype for `computeGridCellMask` (the target masks its result): 7431.

What is controlled evidence and what is one function:

One function; every score is the app's judge on this function. The families are hypotheses tested here only.
Remaining (6367): saved-register order across the actor loop (`explain_regs.py`: the halfword walk's cursors ranked
below the byte walk's), the `$f20`/`$f22` order of the ray distance, and the frame-layout trade above.
