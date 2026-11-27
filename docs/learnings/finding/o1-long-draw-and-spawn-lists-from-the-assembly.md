---
title: o1 long draw and spawn lists from the assembly
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

N64 menu drawers, HUD sprite lists and scene spawn lists of 316 to 2804 instructions: a `switch` on a record's state
halfword, or straight lines, of calls with 6 to 18 arguments. Their saved bests (12,000 to 160,000) were drafts under
`-O2`. All under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`.

The source change, and the measured result before and after (scores, the judge used):

Source generated from the assembly (each arm's calls written out, shared tails repeated in every arm) was exact on the
first compile for four of them, and after a few edits for the others (the app's judge):

- The callee's narrow parameters are in the caller's prototype: an `andi 0xffff` before a call is a `u16` parameter,
  `sll 16; sra 16` an `s16` one (40315 -> 3480 on a 45-arm switch).
- A row product added to a global: the global read into a local of a statement expression first
  (`s32 y = G; s32 z = y + (n + 8) * 14 + 0xE; z + n * 2`) gave the target's load order and add order (3480 -> 0).
- A second float compare of a field just compared loads it again after an empty `do { } while (0);` (115 -> 0;
  `branch_polarity` also reaches it).

Failed experiments that would otherwise be repeated:

Folding the row into one expression (3480), or putting the constant first (4405).

What is controlled evidence and what is one function:

Each item is the measured result on one function. The statement expression is likely a macro in the original (a
hypothesis).
