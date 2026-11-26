---
title: record array for address named indexed tables
kind: function
compiler: gcc-2.7.2-kmc -O1
evidence: observation
date: 2026-11-26
---

An N64 table initialiser (114 instructions, gcc 2.7.2 KMC) clearing seven 0x178-byte records, each with seven word
pairs. m2c names every field by its own address (`*(&D_x + temp)`, `temp = i * 0x178`). Saved best 1480 after 33
attempts. Written as one declared record array (`Row D_x[];`, `D_x[i].member`, the word pairs a member array) it is exact
under `-G0 -mips3 -mgp32 -mfp32 -O1` (-O2: 1810). A per-function profile.

decomp-repair `indexed_symbol_records` writes the array from the target's load and store widths: machine draft 15833 ->
2175, then `masked_counter_loop`, `goto_backward_loop`, `masked_counter_loop` -> 0 (prove_chain). One function.
