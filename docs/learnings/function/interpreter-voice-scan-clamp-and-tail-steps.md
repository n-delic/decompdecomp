---
title: interpreter voice scan clamp and tail steps
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 1315-instruction N64 sound-sequence interpreter of interpreter-arms-inline-helpers-and-tails.md (KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`, the app's judge), continued from its saved best 6125. Not matched: 6125 -> 3230 in this
run, every step a library child now (decomp-repair family in brackets).

The source change, and the measured result before and after (scores, the judge used):

Each step on the previous one (fast_judge, every new best confirmed with compile_and_diff):

1. The pan clamp `t = byte + (s8) cmd; F = t; if ((s16) t < 0) t = 0; else if (t >= 0x80) t = 0x7F;` (the target's
   `sll 16; bltz` on the sum): combine's `num_sign_bit_copies` sees through `(s16)` of a sum of a `lbu` and a
   `sll 16; sra 24` byte (23 sign bits) and tests the whole register. A shift written by hand is a sign-bit comparison
   of an ASHIFT, which simplify_comparison turns into an AND no branch matches, so it stays: `s32 sum = ..; F = sum;
   if ((sum << 16) >= 0) { t = sum; if (t >= 0x80) t = 0x7F; } else t = 0;` 6125 -> 5910
   [`narrow_sign_clamp_shift_test`]. The polarity matters: the same test with the old order was 6215.
2. A division arm `if (D == 0) x = 0; else x = a / D;` as `if (D != 0) {..} else x = 0;`: reorg threads the zero arm's
   store into a `beql` delay slot. 5910 -> 5510 [`branch_orientation`, which read only the first 24 `if`s and offered
   the four smallest swaps; this one is the 33rd `if` and the ninth smallest].
3. `x = (K >> 24 << 16) + (product >> 7)` with the operands swapped (the sum ties to its second operand) 5510 -> 5475
   [`allocation_lever` already offers it].
4. A byte field read into a local moved below the next statement's store to another field: 5475 -> 5025
   [`statement_slide`, which now reads the cast spelling `*((u8 *) (((char *) p) + 0x4A))` as a cell].
5. `if (t != 0xFF) { setSlot(..); } *cmd = 4; r = update(v, &G);` with `*cmd = 4;` in both arms (a new `else`): cse's
   skip-blocks path had carried the register holding `&G` across the skipped call, making the later argument a
   saved-register copy (`la $s0` early, `move $a1, $s0`). 5025 -> 4645 [`join_store_else_copy`].
6. The voice scan, `off = 0; for (i = 0; i < n; i++, off += 0x1A0)` with `u32 n = gCount;`: the offset as `off = i *
   0x1A0;` at the top of the body (loop.c's giv: the zero after the guard, the symbol at each use) 4645 -> 4000
   [`counter_offset_local`, now taking the zero before the `for` and a reused name]; the bound read from the global
   again, the loop's `*((s32 *) (((char *) &flags) + off)) |= 8` as a one-word record member and a `u32` counter of the
   loop's own, together 4000 -> 3870 (the bound alone 5475): loop.c hoists a load only when no store may alias it
   (`invariant_p`, sched.c `true_dependence`: a member store at a varying address cannot alias a fixed scalar of
   another size, the cast store can), and cse2 makes the hoisted load the target's `move $t0, $v0`
   [`loop_global_bound_member_store`]; the scan's temporary split from m2c's function-wide one 3870 -> 3825
   [`local_web_split`]; the key `v = (a << 8) | (b << 16)` as `v = a << 8; v |= b << 16;` 3825 -> 3590
   [`or_compound_steps`].
7. `v = K; if (t <= 0xEA60) v = t << 15;` as `if (t > 0xEA60) v = K; else v = t << 15;` 3590 -> 3565 (with the arm
   results split per case) [`const_arm_conditional_set`, `default_into_then`].
8. `s16 t = a + b; w = t;` as `w = a + b; t = w;` 3565 -> 3560 [`narrow_copy_source_swap`]; a word stored only as
   halfwords in one arm read through a `u16` copy made before the branch 3560 -> 3455 [`halfword_store_copy`]; a flag
   word's `&&` chain of re-reads as nested `if`s on the local it was read into, with the update through the local (in
   one `&&` chain `fold_truthop` merges the three tests into one `and; bne`) 3455 -> 3350
   [`field_chain_local_nest`]; an inline branch helper's `u32 value` as `u16` (the caller's mask after the helper's
   loads) 3350 -> 3230 [`inline_param_width`].

Failed experiments that would otherwise be repeated:

- The command fetch's `lui $v1; addiu $v1, %lo(G); sw (v1); ..; lw (v1)`: in-struct accesses (`((Cmd *) &G)->w0`)
  keep the reload without `volatile`, but cse still folds the address: with `-G8` a 4-byte global is small data
  (SYMBOL_REF_FLAG), its address cost ties a register's, and find_best_addr's tie-break takes the symbol. Declaring the
  global larger than 8 bytes keeps the register but loop.c then hoists every `&G` of the arms into one saved register
  (10516). The target's register means cse did not know the pointer's value at the store; no spelling found.
- The clamp's copy and `andi`: of ~1000 toy spellings (s16/u16/s32 sums and results, shift and cast tests, either
  polarity, sequential `if`s) none gave `move; sh; sll; bltz; move; andi; sltiu` (see step 1 for the best).
- The pitch arms (`lh; bltz; jal conv; move $a0, $v0`): every spelling (ternary, `0 - pitch`, u16 read, a result local,
  an inline helper, one variable for the byte and the pitch) compiles to the same code; the pitch takes `$a0` by its
  copy preference.
- The release code as `case 0:` after case 6 and `default:` last (where the target has them): with the release arm in
  the switch, the last arm falls into the end label and jump.c's first cross-jump rule (insns before the label, minimum
  1) redirects every `a0 = v0; j end` arm into it (7760); with `default:` last too, the nine marker arms cross-jump
  into each other through the old default label (25460). Not solved.
- `(u8)` copies of the variable-write command's index for its call (no change), `u16`/`s16` index types (3650).

What is controlled evidence and what is one function:

One function; every step measured in order with the app's judge, and every family's child reproduces the step's score
exactly from the state before it. The compiler readings (cse address costs and skip-blocks, loop.c invariance, jump.c
cross-jump order) are from the 2.7.2 source and the dumps; the ones not traced insn by insn are hypotheses.
