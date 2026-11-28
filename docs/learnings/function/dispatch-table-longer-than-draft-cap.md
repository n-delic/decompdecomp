---
title: A dispatch switch whose jump table was longer than the draft read, 32583 to 0
kind: function
compiler: SN GCC 2.8.1 N64 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64 -fno-strength-reduce
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 337-instruction loop over 100 pool entries that dispatches on a word field to 74 handlers through a jump table of
1,049 entries (`sltiu ... 0x419`), its address held in a saved register. The compiler and flags are above (per-function).

The source change, and the measured result before and after (scores, the judge used):

The draft read only 256 table entries (`draft_data.MAX_CASES`), so it had 9 of the calls (saved best 32583). With the
whole table read, m2c writes every case. Then, judged by the app's judge:

- the draft: 43687 (a compare tree: GCC counted 93 labels over a range of 1048, below one in ten);
- `switch_table_density` (empty cases for unused values): 865, registers and order only;
- `switch_arm_tail_hoist` (the loop step once after the `if`, not in every arm): 60;
- `independent_statement_swap` (the two loop initialisations): 0.

`prove_chain.py` reaches 0 through these three from the draft. `-fno-strength-reduce` is needed: without it the loop
pass makes a second pointer for `&entry->kind` (12109 against 0 on the hand-written source).

Failed experiments that would otherwise be repeated:

KMC GCC 2.7.2 -O1 (the address range's profile) recorded 2382 on the SN-exact source. A `for` loop with both steps in
its header compiled the same as the `do`/`while`.

What is controlled evidence and what is one function:

One function. The one-in-ten rule is GCC 2.x's `expand_end_case` as read before (hypothesis for SN's build: 93 labels
gave a tree, 105 or more gave the table). Which unused values the game named cannot be recovered from the target: the
empty cases are stand-ins that give the table.
