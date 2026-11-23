---
title: effect spawner loop index forms 2930 to 80
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

The same 1133-instruction effect spawner as effect-spawner-switch-slot-counter-goto-loop.md (four marker slots, a text
panel, a state-byte switch with a 64-step sine table and a pixel loop), KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`.
Handed over at 2930 (81 rows); this session took it to 80 (16 register rows, no order or operand rows).

The source change, and the measured result before and after (scores, the judge used; fast_judge, then the app):

- The first loop written as the game's index loop: `for (i = 0; i < 4U; i++) { if (markers[i] == NULL) { markers[i] = t =
  spawn(...); if (t != NULL) init(markers[i], (char *) s + 0x438 + i * 16, ...); } }` with the call's arguments inline,
  in place of m2c's slot pointer, `0x438` cursor and a copy of it (`__repair_entry_var_s5`) computed before the call.
  The slot address is then a giv loop.c recomputes in the body (`addiu $s4, $s1, 0x418` in the test's delay slot, `move
  $a0, $s4`), the buffer `addu $a1, $s2, $s5` sits in the float compare's delay slot. 2930 -> 2085 (with the table
  offset kept as its own stepped variable; the table spelled `(char *)&D + i * 12` instead gave a pointer giv per field,
  9315).
- The marker reached after the loop as `markers[i]->...` (i is 4 there) at every statement, as the target recomputes
  `sll; addu` per statement: 2085 -> 1875.
- A byte table read in a loop with a computed index (`(&D_8011508D)[layer * 0xA0]`): with the index named in a local first
  the address is `(plus reg symbol_ref)` and the symbol is not hoisted (`lui; addu; lbu %lo` in the body). 1875 -> 810.
  decomp-repair `loop_table_index_local` (#1523). Three spellings of the read itself had compiled identically.
- A stray m2c argument on a renamed callee (`func_80015F0C(0)` for `allocTextNodeObject(void)`): 810 -> 610. The
  target's `move $a0, $zero` was the next loop's counter in the branch's delay slot. signatures.py now reads callees by
  their old address names (decompdecomp #3164).
- The four rodata doubles of the sine loop as literals (`0.1012`, `3.0`, `0.03125`, `4.0`): the phase offset lives in
  `$s4/$s5`, loaded by GAS's `li.d` as `lui $at; lw; lw`, which only a literal gives. 610 -> 470. decomp-repair
  `double_literal_global` takes the register-pair load (#1524), the app hands it the eight bytes (#3161).
- The flags update after the loop as a local updated in steps (`w = M->28; w &= ~0xFC; w |= 0x10; M->28 = w;`): the
  multi-set local is not boosted by sched.c, the block's `sll` goes first and the `and` keeps its operands' registers.
  470 -> 185 (dead_local_stage reached 200 the same way only by reusing an unrelated dead temp). decomp-repair
  `rmw_user_local_steps` takes m2c's cast form (#1525).
- The four parallel field tables as one record array (`D_800C7E10[i].x` for `D_800C7E10/12/14/18` and the `+= 0xC`
  offset dropped): the `i * 12` giv's initialisation then follows the hoisted constants, as in the target. 185 -> 125.
  decomp-repair `record_field_array` (#1527).
- `temp_s6 << 1` and `byte << 3` for two scaled record offsets: `addu $v0, $s2, $v0` (the symbol's register first),
  125 -> 115. decomp-repair `index_shift_spelling` takes parenthesised operands (#1529).
- The fade case's three locals given the names the pixel loop uses (`level` is the alpha, the clamp in place): 115 -> 100;
  and in the pixel loop the row computed before the level copy, with m2c's no-op copy-back dropped: 100 -> 95
  (decomp-repair `copy_back_drop`, #1531). No family reaches the fade case's renaming: it needs two locals to take
  other locals' names at once (each alone is worse), a search over which existing variables a block's values reuse.
- The fade-out case's shared temporary holding the word, the mask moved into its one use (`t = w; v = (t & 0xFF) -
  0x10;` for `t = w & 0xFF; v = t - 0x10;`): the mask is block-local and ties to the subtraction, 95 -> 85.
  decomp-repair `temp_operand_stage` (#1537).
- Which of each dimension's two float variables every read names, searched as a 2^9 grid again at 85: 85 -> 80 (the
  `y` arms and the late `y` reads right, the entry and the `x` arms still swapped).
- Scaffolding taken out at 95 without a score change: a `volatile` pointer read in the fade-out case (written instead as
  the state byte's store first and the pointer read after it, as the target reloads it), a `volatile` float read, two
  `do { } while (0)` around a call and two in the five-iteration loop, the pixel loop's goto (a `while (1)` with
  `break`), an unused `__repair` local. Left: `case 0: break;` in the first switch (245 without it) and the sine loop's
  goto form (its literals are not hoisted).

Failed experiments that would otherwise be repeated:

- Long-lived float pair: every assignment of the nine reads of the two dimensions to the read variable or its copy
  (2^9 grid), both entry orders, `register`, all 24 declaration orders: no better than 120 at 125. Reading everything
  through one variable per dimension deletes the dead copies in the switch arms (2995-3860), and the target keeps them.
  Unsolved: the target keeps `mov.s $f26, $f24` in the arms while `$f26` is never read before the sine loop sets it.
- The fade case compare on the copy (`slti $v0, $a1`) where the target compares the computed value: local-alloc's
  `optimize_reg_copy_1` rewrites a read of a copy's source to the copy when the source dies later in the block. Moving
  the copy, a scope around it, a ternary, an `else` arm, the compare's spelling did not stop it; a 10-line program
  with the same statements compares on the copy in every order tried. Unsolved.
- Natural pixel loop with block locals: 1240 (its registers depend on sharing two temps with the fade case).
- Fresh locals for the fade or the fade-out case: 230-1395 (the shared temps' other uses move).

What is controlled evidence and what is one function:

All one function, judged with the app's own judge; each family was proved by running it on the state before the edit.
