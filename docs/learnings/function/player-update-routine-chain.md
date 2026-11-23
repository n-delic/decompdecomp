---
title: player update routine chain
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 1137-instruction N64 player-update routine (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`), saved best 4316 (m2c's
draft with its goto labels, `var_condition_bit`, an uninitialised pointer local and a hand-sized frame pad). The chain
below reached 60 (two instruction rows reordered) in about an hour of isolated judging, and 0 with the last row (the
word read-modify-write written as a local with separate steps): the function is recorded matched at 0.

The source change, and the measured result before and after (scores, the judge used: fast_judge, then compile_and_diff):

Each row is one edit on the previous source; "alone" rows are the library family that now offers the edit
(decomp-repair PRs merged without a tag).

| step | edit | before -> after | library family |
|---|---|---|---|
| 1 | a halfword field read in an `if` condition kept in a local the body reuses (`(sv = f) == 1` ... `level == sv`) | 4316 -> 4201 | `field_read_condition_local` (decomp-repair #1468) |
| 2 | empty alias labels before `default:` removed and `case 0:` written (the lowest case needs a left neighbour in the compare tree) | 4201 -> 3671 as one child (the labels removed alone 3871, `case 0:` added on top of the aliases 3851) | `default_alias_label_reset` (#1467) |
| 3 | m2c's `var_condition_bit = f > 0.0f; ... if (var_condition_bit)` written as the compare at the test | 3871 -> 2651 | `condition_bit_inline` (#1466) |
| 4 | a call's pointer result copied into a second local at the top of its `!= NULL` guard | 2451 -> 2321 | `guarded_call_result_copy` (#1472) |
| 5 | an uninitialised pointer local (`vec3NormalizeLength(temp_s0_2, temp_s0_2)`) given the address its neighbours use, `temp_s3 + 0x11C` | 2321 -> 2345 (a faithfulness fix, not a search step) | |
| 6 | a field decrement written in place (`if (f > 0) f--;`) instead of through a temp, then the hand-sized frame pad trimmed (the frame grew 8 bytes) | 2345 -> 1960 -> 1940 | existing `product_inline` reaches 1960 |
| 7 | `p = obj + 0x24;` computed before the `if` that sets another call argument, at both calls | 1940 -> 1420 (each call alone 1680) | `call_pointer_argument_precompute` (#1474) |
| 8 | the address-taken frame local that callee filled (`locals.out`) read in the condition and copied again at the top of the block after the calls | 1420 -> 1270 (the snapshot inlined everywhere alone 1470) | `address_taken_snapshot_split` (#1478) |
| 9 | a reused temp (`temp_v1_2`) split for the block that uses it as another value | 1270 -> 1225 | existing `arm_private_rename` |
| 10 | a store moved between a member's store and the statement that reads it back | 1225 -> 745 | `readback_store_barrier` (#1475) |
| 11 | the labelled arm `block_107: h = 0x78;` copied (with the `goto join`) where the other path jumped into it | 745 -> 570 | `goto_arm_label_tail_copy` (#1480) |
| 12 | `!(a < b)` written `!(b > a)` (the operand loaded first changes the luid order) | 570 -> 550 | `comparison_sides` now reads the project's `((Field_T *) ...)->v` cells (#1487) |
| 13 | an address-taken local read once before the pointer store in front of its test | 550 -> 320 | `store_barrier_read_hoist` (#1473) |
| 14 | the copied arm in the other order (after the preceding assignment) together with the holding `if`'s polarity swapped | 320 -> 60 | `goto_arm_label_tail_copy` offers the pair as one child (#1488) |
| 15 | the word read-modify-write `p->w = (p->w & M) | O` written as a local updated by `=`, `&=`, `|=` statements, declared one place above the last local | 60 -> 0 | `rmw_user_local_steps` (#1484) |

Failed experiments that would otherwise be repeated:

- The last row (60 -> 0). The target had `lw $v0, 0x18c($s3)` before `lwc1 $f20, 0x1b0($s3)` in one basic block that also
  holds `sw $zero, 0x1b0($s3)` (the float's clear) and the word's masked update; ours had them the other way round.
  Statement order (all six), the spelling of the mask, int/float zero stores, temps for either value, `-mcpu=r4600`,
  `const` casts and `do { } while (0)` barriers did not move it. `sched.c` with `-dS` notes (the `;;` lines of the sched
  dump): the scheduler works backwards and gives an insn that makes a register live (`birthing_insn_p`: the destination
  pseudo is set once) the launch priority `7f000001`; the word load, a pseudo set once, took it and was launched first,
  which puts it second in the output. Written as a user local with three sets (`w = p->w; w &= M; w |= O; p->w = w;`) it
  gets no boost and the float load, with a hazard of one cycle against the store before it, comes second, as in the target. A 25-line
  program with the same three statements reproduces both orders. Where the local is declared mattered (above the last
  local: 0 / 60; below it: 660): pseudo numbers feed the allocator's tie-breaks.
- Adding `case 0:` to the unreduced label list (3851) or removing the aliases alone (3871) were worse than the pair.
- `var_a1`/`var_a0_3` took each other's registers until the arm that assigns `0x78` was duplicated and its stores ordered:
  the allocation priority is `floor_log2(refs) * refs / live`; 4 refs over 21 and 22 insns give 0.381 and 0.364 and the
  higher one gets `$a0`.

What is controlled evidence and what is one function:

Every row is measured with the exact judge on this one function. Each new family reproduces its step from the pre-state
(the child's score, or its text, equals the manual source's); the rows marked existing were found by asking the whole
library for children of the pre-state (`draft_repairs.variants`). The last row's cause (`birthing_insn_p`) is read from
the scheduler's notes of one block and a 25-line program, not proved in general.
