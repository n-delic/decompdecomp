---
title: memory setup boot variable and cursor reads
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

A memory set-up routine (88 instructions) of an N64 game: it reads the memory size, patches two entries of a region
table, then builds a 16-entry list from that table. Flags: `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`.

Each of these edits was needed, and measured by undoing it alone from the exact source (app judge):

- `n = osMemSize;` builds the address in its own register (`lui $v0; lw $a3, 0x318($v0)`). `n = *(u32 *) 0x80000318;`
  is expanded by the assembler into the destination (`lui $a3; lw $a3`): 410 -> 0. Family `boot_variable_literal`.
- `T[2] = n - 1 + 0x80000000;` folds into `n + 0x7FFFFFFF`. `n--; T[2] = n + 0x80000000;`: 1465 -> 0.
  Family `shared_decrement_in_place`.
- `while (*e < 0x80000000)` with `start = 0x80000000;` after it. Set `start` before the loop and compare with it:
  1280 -> 0. Family `constant_bound_local`.
- `end = e[0]; attr = e[1]; e += 2;` as `*e++` twice: 920 -> 0. Family `consecutive_postincrement`.
- One local for the memory size and, later, the list counter (the same register in the target): 330 -> 0. The
  existing `disjoint_local_merge` already finds it.

Controlled result: with the first four undone at once (3805) a greedy chain of the four new families reaches 0.

Not covered: the saved best this started from (1480) came from another game and stored to that game's symbols where
the target stores at offsets of this game's region table. No family rewrites such a store from the judge's rows; the
function was matched from a rewrite of the target's assembly. One function: the rules are observations.
