---
title: A 370-instruction mixing-buffer function: post-increment through two locals, a struct-member store, staged call arguments
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 370-instruction audio mixing-frame function (an N64 game, KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): a
sound-slot walk, a cursor flip between two buffer pairs, then three counted loops that each call a mixing routine with
`buffers[(u16) index]` and `(u16) counter`, then a deferred-callback test and a task build. Saved best 1680 after 100
attempts and 25,000 automatic trials; the differences were the order of one store and a compare, two saved registers
exchanged in three loops, and a few reordered loads. Nothing in the saved best was structurally wrong, which is why a
greedy chain never moved it.

The source change, and the measured result before and after (scores, the judge used; fast_judge, then the app's
compile_and_diff for 220 and 0):

Each row is an edit to the saved best, its score alone and its score after the earlier rows:

| edit | score | what it changes in the compiler |
| --- | --- | --- |
| loop 3's `inline_fn(gWorkerActive, 0)` written as the `&&` of the other two loops | 1680 -> 1675 | no boolean temporary (`sltiu`) |
| `*D_P = 0;` through a global pointer written `((Rec *) D_P)->first = 0;` (`typedef struct { s16 first; } Rec;`) | 1675 -> 1560 (1680 -> 1610 alone) | the store is "in struct": `true_dependence` (rtlanal.c) lets the next global load pass it, so the compare's load is first and the store sits in the branch's delay slot |
| `var_s1 = x & 0xFFFF; temp_s2 = var_s1;` written `temp_s2 = x & 0xFFFF;` | needed with the row above | no relay copy filling the delay slot |
| staged call arguments `temp = (u16) counter; counter += 0xC0; ++count; call(t[i], temp);` written `call(t[i], (u16) counter); counter += 0xC0; ++count;` (loops 1 and 3) | 1560 -> 1150 (one loop 1355) | `andi $a1` follows the table load as in the target |
| `index = cursor++;` written `tmp = cursor++; index = tmp;` in all three loops | 1150 -> 220 | `tmp = cursor; cursor += 1; index = tmp`: combine cannot merge the copy across the increment, so `move $v0,$s0 ... addiu ... move $s2,$v0` as the target has it, and the allocator ranks `index` by two references |
| `u32 cb = sWorkerPendingCallback;` read at the end of the cursor flip, `if (cb) call(p, cb)` | 220 -> 100 | the `lbu $a1` joins the run of loads before the `xori` |
| the counter set after the call that follows it (`func(); counter = 0;`), the identical-arm `if (var_s0)` guard over the join before the compare made a real `if (gWorkerActive)`, the outer `do { } while (0)` fences opened, and a `do { } while (0)` round the cursor flip's two reads | 100 -> 0 together | priorities counter > index > count (global.c `find_reg`), the compare in its own block so cse cannot forward the reload in the else arm, and a scheduling boundary before the address arithmetic |

Final: the app recorded 0 (matched, byte-identical, 370 instructions). The match relies on scaffolding: an
identical-arm `if (gWorkerActive) {A} else {A}` before the compare (both arms are the same four statements, merged by
cross-jumping; the real source may have had a macro or a conditional that left no code), two single-use locals for the
post-increment (`tmp`/`index`), the early `u32` copy of the byte global, and the `do { } while (0)` around the cursor
flip. Each is a faithful program (no dead stores, no phantom locals), but none is a source a developer necessarily wrote.

Failed experiments that would otherwise be repeated:

- Removing the saved best's scaffolding (the `if (var_s0)` read of an uninitialised local, the outer
  `do { } while (0)` fences, the two `if (a || b) x = 0; else x = 0;` tails) one at a time: each is worse alone (1560 ->
  1860 for the guard) because the fences weight every reference by loop depth and the guard splits the block before the
  compare; they have to be replaced together by something that does the same job.
- Reading `D_800E4268` once into a local for both the compare and the else arm: cse merges the arms' loads whenever the
  load and the compare share a basic block; the target's reload means a label (a join) sits between them.
- A `u8` local for the early byte read: `andi $a1,$a0,0xff` appears; a word local takes the zero-extension at the load.
- Wrapping the counter's update in `do { } while (0)` ladders 1..7 deep to raise its priority: every depth scored 1300+
  (the wrapper changes the schedule more than the weights).
- Moving the early read to the stores' line (after the cursor flip's address arithmetic): 220 (no change).

What is controlled evidence and what is one function:

Controlled: each family below was run on the saved best 1680 with the exact judge (the rows above are the measurements).
One function: the combination in the last row, and which of the two `do { } while (0)` placements preserve the reload
(any outer loop does; the loop-depth weights then decide the registers). The compiler explanations (true_dependence's
`MEM_IN_STRUCT_P` rule, combine's `reg_set_between_p`, find_reg's priority order, cse's path following that needs a
BARRIER before the else label) are read from the compiler source (rtlanal.c, combine.c, global.c, cse.c); the effect
of each is measured, not isolated.

Library families (decomp-repair, #1347 and #1348): `deref_member_store`, `postinc_copy_split`,
`call_arg_late_update`, `early_global_byte_copy`, then `relay_copy_fold` (the relay row), `helper_call_expand` (the
`inline_fn` row; 515 -> 190 in the chain's state) and `fence_regroup`, which offers the last row as one child (100 -> 0;
its single steps `fence_open`, `tail_guard_collapse`, `join_guard_flag`, `init_after_call` are offered too, none of them
alone better than 100). `prove_chain.py` from the saved 1680 with the first four reaches 515 (1110, 710, 590, 515) and
the existing families add only `arm_private_rename` (425); the three new ones close it. A search over which of the
last row's edits are needed found only the full set of seven (fences opened x3, tail collapse, real condition, reset
after the call, fence round the last two statements) reaches 0.
