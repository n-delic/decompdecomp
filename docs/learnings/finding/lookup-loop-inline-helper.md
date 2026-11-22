---
title: lookup loop inline helper
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-22
---

What the function looked like: a 588-instruction sound-entity function of an N64 game that searches one table (a busy
byte and a key word at a 0xFF8 stride, eight entries, the id's sign bit carried into the index) seven times: for the
event's own id, its target id, and to set parameters on either. The saved best (190, 38 register rows, instructions
already the target's) was m2c's goto copies: a counter, stride and key variable per search, `found_N:` stubs,
`join_N:` labels and `goto skip_N;` hops, with `temp_a1` (the id) and `var_a1_2` (the slot) shared by the searches.
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`.

The source change, and the measured result (isolated judge, `fast_judge`, scores as the app records them):

1. Each search written as a call of one `static inline s32 find_entry(s32 id)` (the loop as the matched sibling
   functions wrote it: `for (; i < 8; i++, off += 0xFF8) if (busy[off] == 0 && key[off] == key) return i | (id & 0x80000000);
   return -1;`), stubs, labels, setup and hop gotos deleted: 190 -> 350 (instructions identical, 70 register rows).
   Worse than the saved best, which is why a greedy search never takes it alone.
2. Each search's id and slot as one local of its own (`P = EXPR; res = find_entry(P); P = res; if (P != -1) {..}`,
   declared in the block that holds the run), as the helper's `arg0 = find(arg0)` caller had it: 350 -> 20.
3. `arm_scoped_scratch` (an existing family) moves the declaration of one more scratch into its arm: 20 -> 0. The u16
   and the word loaded for the call that follows swap saved registers by declaration order.

Why (hypothesis, not confirmed in the compiler's source): GCC 2.7.2 expands an inline call into new pseudos, so every
search is allocated by its own short live ranges. Shared `temp_a1`/`var_a1_2` are one long-lived pseudo that
outranks the counters (59 references over 142 live insns against 5 over 16) and takes `$a0` in every region; the
target has the counter in `$a0` and the id/slot in `$a1`. `passes.py` showed the order; a single-name merge of every
join variable (`temp_a1` everywhere) gave 445, one loop counter shared by all searches gave 1249 (it lived across
calls and went to `$s0`), and folding the id load into the call or forwarding the result copy gave 880 to 2005.

Library: decomp-repair `lookup_loop_inline` (helper) and `lookup_loop_inline_private` (helper plus the private locals,
one child); `prove_chain.py` on the saved best: step 1 `lookup_loop_inline_private` 190 -> 20, step 2
`arm_scoped_scratch` 20 -> 0.

Failed experiments that would otherwise be repeated: a clean rewrite that also inlined the stop/set bodies matched too,
but nobody can tell from it which part mattered; keep the minimal edit list above. The family needs two or more
searches with identical tables, strides and limits.

What is controlled evidence and what is one function: one function (verified exact in the app), the family test
covers the rewrite on a small synthetic of the same shape. The register effect is measured here, not proven for other
functions.
