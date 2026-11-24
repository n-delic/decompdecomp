---
title: switch join block host and order
kind: finding
compiler: sn-n64-281 (SN Systems GCC), -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-24
---

A 31-instruction N64 predicate: a `switch` on a call's result whose two value arms set a constant and meet at one
`if (temp == var) { <seven case labels> return 1; } return 0;`, the default `return 0;`. m2c wrote the block inside the first
arm and the second arm `goto`s it. The draft judged 570.

The compiler lays the arms out in source order and the block directly after the arm that falls into it, so the game's
source (block in the *other* arm, arms in another order) is different bytes, and nothing that reorders whole arms moves
the block itself. Both edits had to be made together:

    before: case 0x12: v = 19; block_4: if (t == v) { case ...: return 1; } return 0;
            case 0x20: v = 33; goto block_4;   default: return 0;
    after:  case 0x12: v = 19; goto block_4;   default: return 0;
            case 0x20: v = 33; block_4: if (t == v) { case ...: return 1; } return 0;

Measured (decompdecomp's judge from a scratch worktree, nothing recorded): the draft 570 -> 0 with the library's
`switch_join_host` in one step (`prove_chain.py --families all`). The best the older families reached from the draft was
20 (`switch_layout`: the block's arm moved last, but the *other* arm's constant first).

The same function has a second, quieter state. With the two arms' constants exchanged (`case 0x20: v = 19; goto; ...
case 0x12: v = 33; block_4:`) every instruction is exact and only the jump table's data differs: the judge says 1, its
diff has no rows. Exchanging the constants from the 20 state gave 1 (`switch_join_values`); the case numbers the table
disagrees on are then `linked_switch_labels`'s rows (1 -> 0). That family used to decline any function containing a
`goto`, which is every m2c switch of this shape; it only exchanges case numbers, so it no longer does.

What the target's jump table says: the block's own cases (the seven labels) enter at an address above the arm that holds
the block, so the arm with the highest address below them is the block's host; arms with a body of their own are in
address order, but a bare `return`/`break` arm is not (the compiler merges those tails: the default entered the shared
`return 0` after the block, though it sits in the middle of the source).

The judge worker now prints those rows in its diff (`Linked jump-table differences (case | target | compiled)`) and returns
them as `jump_tables`, so a score of 1 with no rows says which cases to look at.

Failed experiments that would otherwise be repeated: the other spellings the hand session tried (not itemised) scored 570
to 970; the plain statement-order grid cannot reach it, since the value-to-case mapping is the difference.

What is controlled evidence and what is one function: one function. On the project it came from the host family fires
on 34 of 2206 functions' m2c drafts (1.5%, 17 children each on average, at most 48), all of them goto-join switches.
