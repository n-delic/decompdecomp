---
title: A loop counter increment copied into every arm of an if chain, and a count read before the pointer copy
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 92-instruction loop that fills byte tables by case (gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`, exact; the saved best
was 600 under that profile and 1425 under the project default).

- The saved C had `n++;` at the end of every arm of a nested if / else-if chain. Written once after the chain: 600 -> 180
  (the target's `j` delay slots hold the common increment). decomp-repair `chain_tail_hoist`.
- The two stores of one arm in the other order: -> 115.
- The entry test reads the count through the global into a local before the loop's pointer is copied
  (`c = g[2]; p = g; if (c != 0)`): -> 0. decomp-repair `global_pointer_site_local` reached 0 from 115 too.

Evidence: the app's judge. `prove_chain` from the saved best reaches 0 with `chain_tail_hoist`,
`independent_statement_swap`, `allocation_lever`, `global_pointer_site_local`.

Failed: a `for` / `while` loop whose condition assigns the pointer (500: the count stays in a register).
