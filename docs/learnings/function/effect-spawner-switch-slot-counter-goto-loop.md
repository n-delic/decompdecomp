---
title: effect spawner switch slot counter goto loop
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (N64)
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 1133-instruction effect spawner (an object that lays out four marker slots, a text panel, a switch over a state byte and
a 64-step sine table), KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`. The saved best was an m2c draft at 7355 (7905 as
re-judged), mostly register rows and a handful of layout rows. Not matched in this session (3020 at the last submit); the
steps below are what moved it, each now a library family or a measured lead.

The source change, and the measured result before and after (scores, the judge used; fast_judge, one source at a time,
every edit judged against the one before it):

- `volatile u32 reserved[2]` frame padding dropped once the spilled `sp1C` pointer was in the frame: 6305 -> 6265 (the
  frame was 8 bytes too big). Saved bests with such padding are worth re-testing after any other edit.
- m2c's `if (n != 2) { if (n >= 3) { if (n < 5) B } } else if (flag == 1) A1 else A2` as `switch (n) { case 2: ...; case 3:
  case 4: ... }`: the dispatch lays out as `beq, slti, slti` with the single-value arm first; region 0 of the diff went from
  18 differing rows to 1 (`embedded_if_tree_switch`, decomp-repair #1462; `if_tree_switch` only read a tree that was the
  rest of a function, with single values and no `else if`). An empty `case 0: break;` then moved the compare constant
  (3275 -> 3125, `switch_layout`, found by the library).
- A record member reached twice in one loop block (`*((void **)(var + 0x418))` stored and read as an argument) held in a
  pointer local at the head of that block: 7905 -> 6798 (`member_slot_pointer_local`, #1464). The local must sit inside
  the `if` body: set above the `if` it becomes the loop's induction variable (`addiu $s1, $s2, 0x418`) and scores 7905.
- A constant word store the target stores as a byte (`*((s32 *) (p + 0x28)) = 0x80;` for `sb`): 6305 -> 6105
  (`narrow_store_width`, constant sites, #1471).
- A literal stored after a `(u32) f` conversion held in a local set before the float arithmetic, and stored before the
  converted value's store: 6120 -> 5630 (`constant_store_early_local`, #1483). Each half alone: +5 / 0.
- A later loop counted with the first loop's counter, read after that loop (a table index): 6040 -> 6000
  (`loop_counter_reassign`, relaxed, #1486).
- A call's pointer result tested for NULL and used throughout the arm, copied into a local of its own at the arm's head:
  5630 -> 5560 (`call_result_arm_local`, #1491).
- The sine-table loop written as a goto loop (`loop_goto_form`, #1501) (`label: { ... } if (c) goto label;`) instead of
  `do { } while`: 4035 -> 3555. The target does not hoist the loop's `lui/ori` float literals, a double global's second
  word or an address `lui`; loop.c does for a `do`/`while` loop, so the saved registers it spent are free again. And
  moving `var_fs2_2 += 0.21f` below the statement that calls `__cosf`: 3555 -> 3215 in that state (scheduler order across a call).

Failed experiments that would otherwise be repeated:

- Literals for the rodata doubles (`phaseOffset = 0.1012;`) are worse than the externs (6305 -> 6640): the candidate's own
  `.rodata` is a different address. `const` on the externs changes nothing.
- Spelling the byte-table read three ways (`(&D)[i * 0xA0]`, `D[i * 0xA0]` with an array declaration, the cast form) compiles
  identically: the cause of the hoisted `la` is elsewhere (loop.c's threshold, not the expression).
- A hill-climb that swaps adjacent statements ("accept if the score is lower") accepted `temp = var_fv1 - 48.0f;
  var_fv1 = 2.0f * h;` (a read before its set, 60 points better): every swap needs a dependency check, and the judge's
  score is not evidence of a faithful program.
- Merging every counter into one variable (all three loop counters) is worse (6895) where merging the one counter the
  target shares is better.

What is controlled evidence and what is one function:

All of it is one function, judged with the app's own judge (`fast_judge`) after each edit; the library children were
checked against the state before each edit (`reach`: the library's ~2700 children did not contain it before these
families). The statement-order step (`var_fs2_2 += 0.21f` below the call, `statement_slide` past a call, #1507) and the other steps each have a family.
