---
title: a 1300-instruction controller routine, 10695 -> 0 (merged-arm joins, float flags, a statement-macro scope)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

An N64 per-frame controller-input routine (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, 1300 instructions): mode
toggles from bindings, a table walk back to the previous enabled enemy definition, a text-node colour chosen by mode,
float smoothing of stick axes with "near zero" tests and clamps, and a mode switch at the end. The saved best was 10695
(isolated judge = app). Earlier sessions had released it as too large.

The source change, and the measured result before and after (scores, the judge used):

Isolated judge (`fast_judge.py`), each confirmed in the app; the library family that now offers each step in brackets.

1. Table reads at the target's load width (`lb`/`lw` of `%lo(SYM)`), a search's identity helper dropped: 10695 -> 9925
   (`asm_symbol_read_width`); `-((int) 0.0002f)` -> `-0.0002f`: -> 9840 (`float_literal_int_cast`).
2. A switch that only picks a colour, followed by its store and a flag read-modify-write: an ascending `if`/`else if`
   chain with both statements in every arm (the target has the RMW's `lw` at the head of each arm: jump2 cross-jumped
   the arms' identical tails): 10425 -> 7715 (`switch_assign_if_chain`).
3. `__base_SYM = SYM;` written out (-> 7550, `symbol_base_inline`); a table index as `s32` and the step as `s32`
   (-> 7330, `load_width_local`); stray m2c call arguments past the callees' arity (-> 6935, `callee_arity_trim`).
4. `if (!(x != 2)) A else if (x != 5) D else B` as `switch (x) { default: D; case 2: A; case 5: B; }` (default first:
   gcc's `beq` chain falls into the first-written code): 6935 -> 5315 (`negated_chain_switch`).
5. `var_condition_bit` set to a different compare in each arm and tested after the join: the test in each arm with
   the store between: 5315 -> 4645 in two steps (`condition_bit_arm_tests`).
6. Float locals merged (m2c split one reused temporary): 4645 -> 4470 (`disjoint_local_merge`).
7. m2c's float clamp `v = -K; if ((x < -K) || ((v = K, x > K))) F = v;` as two arm stores, all six together:
   4470 -> 2380 (`clamp_arm_stores`, float form).
8. The axis result into the reused float (2380 -> 2245), the "not near zero" flag pairs as `!(x <= K) | !(x >= -K)`
   in the flags' order: 2245 -> 1215 (`store_flag_compare_inline`).
9. `t = 0.4f; if (C) t = 0.1f;` as `t = (C) ? 0.1f : 0.4f;` (gcc computes C first, then loads the default):
   1245 -> 610 (`guarded_constant_ternary`).
10. The search loop as a `do { } while` over a typed `EnemyDef sEnemyDefTable[]` (a char-pointer sum hoists `la` out of
    the loop; the typed row keeps `lui $at` in it), the step and the loop's start as `s8` (420 -> 290, `load_width_local`),
    the products `idx * 8`, `idx * 0xE8` written at their uses so cse shares `idx << 3` (290 -> 180,
    `product_local_inline`), `p->idx += d` (180 -> 125 and the target's 8-byte frame slot, `loaded_field_compound`),
    the record pointer sunk into the one-arm `if` that uses it (125 -> 85, `arm_invariant_sink`).
11. `x = F / k` as `x = F; x /= k` (`compound_operand_split`, field operands), `if (B > A) B = A` and the in-place
    product through a new local (550 -> 500, `float_clamp_reads`), `owner->rec[i].field` for `owner + i * 8 + off`
    (base-first `addu`: 75 -> 65, `record_row_member`).
12. Last 65 (thirteen rows, two float locals exchanged in `$f20`/`$f24`): the stick value had 7 references over 56
    instructions (0.25) against the scale's 6 over 36 (0.33). Its guarded `if ((m != 2) & (m != 5)) x = getAxis(..);`
    in `do { } while (0)` gives it 8 references (0.43): 0, recorded by the app (`result_weight_scope`). This is a
    statement-macro shape; if the game had no macro there it is scaffolding.

Failed experiments that would otherwise be repeated:

- A `switch` with the stores in its arms (either case order) instead of the `if` chain: 5645-5725 vs 4645.
- The colour selection as one conditional expression: 10055. Hoisting the flag RMW's read above the chain: 11680.
- The table read inside the loop spelled as `((u16 *) T)[i * 0x74]`, `((Row *) T)[i].f`, `&T[i * 0xE8]`: identical code
  to the char-pointer sum (spelling-invariant); only declaring `sEnemyDefTable` itself as a struct array stopped the
  `la` hoist. A goto loop also stops it but loses the target's 8-byte frame slot (two stale pseudos: the narrow step's
  copy and the field update's).
- Splitting the scale into one local per arm scored 475 but reads an uninitialised value in one path (rejected).
- Raising the stick value's references by testing it instead of its copy: cse rewrites the tests to the copy (no
  change); merging it with the later generic float: 240.
- `addu` operand order: every pointer spelling, byte-array members and declaration order compile product-first;
  only a record with a real field of the access's type gives base-first.

What is controlled evidence and what is one function:

One function. Each step's before/after is measured with the app's judge and every family reproduces its step from the
recorded intermediate source (isolated judge). The priority arithmetic (`floor_log2(refs) * refs / live`) is read off
`-dl -dg` tables; the cross-jumping explanations are from jump.c's `find_cross_jump` and the target's delay slots;
both are hypotheses for other functions.
