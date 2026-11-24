---
title: interpreter arms inline helpers and tails
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

A 1315-instruction N64 sound-sequence interpreter (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, judge: the app's): a
loop that copies a two-word command into globals, switches on its low 7 bits over a 0x72-entry jump table, and stops
when a handler returns nonzero or after 0x20 commands. The saved best was 24495 by an old header (29955 when rechecked),
full of search scaffolding (a hoisted `0x51EB851F` local, a `-1` local, `((u32)(x & M) - 1U) >> 31` for a negated bit
test, a phantom `long long`, `volatile` reads of the command word). Not matched: 29955 -> 6125 in this run.

The source change, and the measured result before and after (scores, the judge used):

Each step on the previous one, the library family that now offers it in brackets:

1. Nine marker opcodes that differ only in a flag bit and two list offsets, written as one `static inline` helper
   taking `(voice, list, mask)`: the bit becomes a parameter, so fold-const never sees a constant single-bit test
   (`and; sltiu; sltiu; or` with the bit in a register that the `|=` reuses) and the list base is computed before the
   test, 29955 -> 22323 (22863 with the mask before the list) [`switch_arm_inline_helper`].
2. The goto loop as `do { } while (++count < 0x21)`, the hoisted divisor and `-1` locals written as `/ 100` and `-1`:
   loop.c hoists them into `$s4`/`$s5` itself, 22323 -> 19185 [`guarded_goto_loop`].
3. Every arm's `result = 0;` moved to its `break;`: one arm setting it before a call makes the result a saved-register
   pseudo everywhere; each of the two arms alone was worse (19245, 19385), together 15930 [`arm_result_constant_sink`].
4. m2c's `(u16) D_800FDB9C` / `(s16) D_800FDB9C` of a word global where the target loads `lhu`/`lh` at the symbol's
   own address (the high half on big-endian), written `*(u16 *) &D_800FDB9C`: seven reads 16130 -> 14715
   [`word_global_narrow_read`].
5. Arms that store the command word after a call keep its address in `$s0` from the arm's start: `u32 *ptr =
   &D_800FDB9C; ...; *ptr = 4;`, 14360 -> 13735 [`arm_global_store_pointer`].
6. m2c's `default:` label inside an arm's `else`, written as the arm falling through into `default:` (the next arm,
   identical but for one offset, stops being cross-jumped into it), 13735 -> 11545 [`default_label_fallthrough`].
7. `if (g != 0) { r = 0; if (g != 1) {} else {...} } else {...}` as `switch (g) { case 0: ... case 1: ... }`,
   11300 -> 10360 [`nested_test_switch`].
8. m2c's `goto` to a label at another arm's own level (a shared call, the shared `result = 0; break;`), copied into
   each arm: the compiler's cross-jumping picks which copy survives, 10360 -> 9155 [`goto_arm_tail_inline`, new
   arm-level child].
9. A 0/1 ownership flag set by an `if` tree, as an inline predicate with `return 1;`/`return 0;` (jump.c no longer
   makes a store-flag `xor; sltiu`), 9595 -> 9000 [`flag_predicate_inline`].
10. m2c's `if (n != 0) { off = 0; do { ... } while (i < n); r = 0; } else { r = 0; break; }` as a `for` with the
    offset stepped in the increment, 8280 -> 6141 [`guarded_do_while_for`].
11. A `for` adds an 8-byte frame slot: the saved best's unused `volatile u32 frameSpace[14]` cut to 12 to keep the
    target's 0x78 frame, 6141 -> 6125 (scaffolding: the real locals behind those 48 bytes are not known yet).

Smaller hand steps: a branch helper for the four "jump if value >= byte" opcodes reading the command word before the
compare (`lw; srl 16` rather than `lhu`), the 0xB clamp as one nested ternary (dropping the phantom `long long`), the
0x1A clamp storing the raw sum and then the clamped value, the two-byte comparison of the slot check in `s32` locals.

Failed experiments that would otherwise be repeated:

- Every arm's `goto block_60`/`goto block_205` replaced by `result = 0; break;` while the shared label stayed
  elsewhere: worse (15175), the merged copies land in other places; the arm-level copy (step 8) is what works.
- `default:` moved to the end of the switch (where the target has it, `.L...2F8: move $a0, $zero` falling into the
  loop test) and the release code written into case 6 / case 0 (where the target has it): each alone or together
  makes jump2 cross-jump the marker arms and the arithmetic arms into each other (8280 -> 12685 / 27695). The
  target's layout needs the order in which jump.c meets the arms' identical `move $a0,$zero; j` tails; not solved.
- The fetch's `lui $v1; addiu $v1, %lo(D_800FDB9C); sw $v0, ($v1); ...; lw $v0, ($v1)`: a non-volatile pointer local
  folds to `%lo` and drops the reload, a volatile one reloads through `%lo`, an inline fetch helper with a `u32 *`
  parameter folds too (6480).
- The 0x1A clamp's `sll 16; bltz` on the unclamped sum: `(s16)` casts, `& 0x8000`, `s16`/`u16`/`s32` locals, reading
  the field back: GCC always knows the `lbu + sra 24` sum fits and tests the full register.
- `i * 0x1A0` indexing (or a typed `[0x68]` row) in the voice scan instead of a stepped offset: worse (7288).

What is controlled evidence and what is one function:

One function, each step measured in order with the app's judge (`fast_judge`); every family's child was scored on
the same states and reproduced the hand step's score, and the m2c draft goes 69922 -> 46189 with the families chained.
The compiler explanations (integrate.c parameters hide constants from fold, jump2's cross-jump order) are hypotheses
read from the output.
