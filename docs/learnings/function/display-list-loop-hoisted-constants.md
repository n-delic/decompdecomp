---
title: display list loop hoisted constants
kind: function
compiler: KMC GCC 2.7.2 (gcc-2.7.2-kmc), -O2 -G0 -mips3 -mgp32 -mfp32
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 313-instruction display-list builder (N64, KMC GCC 2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`): a `do { } while` over a
list of nodes, four arms that each append two or four GBI commands (colour words built from a node's colour word:
`hi byte`, `& 0xFF0000`, `& 0xFF00`, an alpha byte), the global cursor advanced one command at a time, two call
arguments that are float conversions of loop-invariant sums, and a byte `1` flag spilled to the frame. Saved best 2760.

The source change, and the measured result before and after (scores, the judge used; isolated `fast_judge`, the app
recorded 0):

2760 -> 0 in seven edits, each named so a family can offer it:
1. the hand-made `volatile struct { int unused; int filter; } stack;` + `#define sp14 stack.filter` frame written as
   `s32 sp14;` (2760 -> 2750; also why no local family ran: the `#define` made `view()` decline the body) -
   `volatile_stack_struct_drop`;
2. `x & (0xFF0000 & 0xFFFFu)` (which is `x & 0`: the byte was missing) written `x & 0xFF0000`, with its `|` chain in the
   order of its sibling (2325 -> 2155) - `literal_mask_conjunction`;
3. the byte copy `u8 v = (u8) x` read in the stores instead of `x & 0xFF`, the `v = x & 0xFF;` resets dropped, the
   test of it written on the word (2185 -> 2155; chain then 0) - `narrow_copy_reuse`;
4. the two float conversions written at the call sites, not as locals set before the loop (2455 -> 2155) -
   `conversion_local_inline`;
5-7. the display-list stores as GBI macros (`gDPSetPrimColor(cursor++, ...)`, colour masks as `_SHIFTL` shifts), their
   statement order, and the `Field_` struct reads: the existing `gbi_macro_commands`, `statement_group_order`,
   `field_struct_view_printed`, 2155 -> 0 by `prove_chain`.

What decided the register assignment, read from `cc1 -dl -dg -dL` and `loop.c` / `global.c` (the rule, not the spelling):
- `loop.c` moves every constant whose `threshold * savings * lifetime >= insn_count` (`move_movables`); here `1`, the
  four `0xFA000000`, the four `0xFB000000`, `1.0f` and (written `x & 0xFF0000`) `0xFF0000` were all hoisted. The
  preheader order is the order of each group's first insn in the loop, and a hoisted pseudo that gets no register is
  rematerialised at each use (a `lui` per use).
- All of them tie on `floor_log2(refs) * refs / live` (4 uses at loop depth 2 plus the set: 9 refs), `live` is the
  distance from the set to the loop end (set order), `allocno_compare` truncates the priority to an int and breaks ties
  by register number. Whichever constant is set last wins the last saved register (`$fp`).
- Writing `(c & 0xFF0000)` as `((c >> 16) & 0xFF) << 16` makes the `and`'s constant come from combine's split of an
  invalid operand: a fresh pseudo set right before each use, after the loop pass, never hoisted. That left `0xFB000000`
  the only contender and gave the target's `$fp` (2155 -> 500 with that alone). `gbi_macro_commands` writes the
  `_SHIFTL` form, which is why it reaches the target.
- Display-list stores through a struct member (`g->words.w0`) do not alias the cursor global (`MEM_IN_STRUCT` against a
  fixed address), so cse keeps `cursor == p + 8` across them and the second command's `lw` of the cursor disappears:
  the `+8` store and `+0x10` store pair of the target. Raw `*(s32 *)(p + 4) = x` stores alias it and cse drops the
  first cursor store as dead when they are adjacent.

Failed experiments that would otherwise be repeated:

- Wrapping one `0xFB000000` store in `do { } while (0)` (10 refs) flips `$fp` to it but then outranks the two
  conversion locals (7 refs, `.070`) and takes `$s6`; the target has `1`, `iy`, `ix`, then the constant. Reordering
  statements (two 720-order grids and 3000 random pairs) never moved it: live length is the set order.
- `(c & 0xFF0000)` with the stray fourth call argument removed: the pointer then takes `$t0` instead of `$a3` and the
  frame grows; the 4th argument (a draft's) is scaffolding the match still relies on.

What is controlled evidence and what is one function:

Controlled: each of the four new families applied to the ablated source reproduces its step (scores above, isolated
judge). One function: the loop.c arithmetic (`insn_count` 237, threshold, `savings`/`lifetime` of each constant).
Scaffolding the final source relies on: the `u8` alpha copy `var_v0`, `do { temp_s2 = ... } while (0)` around the second
colour word's load, the `__base_D_800C0738` base pointer, the extra `temp_a3_2` call argument, and `(volatile)` loads of
the colour word.
