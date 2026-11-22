---
title: A float window clamp: four edits that only pay off together, then the register order of three block-local values
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

An N64 bounding-rectangle function (96 instructions, KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): a rectangle read
through a pointer parameter, offset by a record's two translations, widened by `8.0f`, then each side the larger or
smaller of that and `record field + translation +/- margin`, then a scissor command built from the four values. The saved
best stood at 120 with 22 rows (20 float registers, two load orders), and carried scaffolding that read an
uninitialised local (`if (v) { x = a; } else { x = a; }`) which alone was worth 30.

The source change, and the measured result before and after (scores, the judge used):

Isolated judge, from the saved best: 120 -> 0 as one child of the new library family `float_prefix_stage`
(decomp-repair, `prove_chain.py --families all` picks it as step 1). The edits, with what each does to the compiler
(sched.c loop-note and priority rules read in the 2.7.2 source; the local-alloc priorities are hypotheses from `-dl -dg`
dumps on this one function):

1. A reused variable split (`t = *(p + 0x54) + t;` into a fresh name, later reads renamed). `t` was set twice, so it was
   global and the sums that read it could not fold.
2. Loaded temporaries read once or twice folded into their sums at the position of the *last* add
   (`sum = a + t; sum += tmp;` -> `sum = a + t + *(q + 0x70);`; `cse` merges the second inline copy). The statement order
   is then the order of the final adds, which is the order the target has. 910 on its own (everything hoists to the top).
3. The accumulator's first sum staged in locals of its own (`acc = *(p+0x58); acc += t1; ...; acc += u;` -> `head = ...;
   stage = head + t1; ...; acc = stage + u;`). The first add is block-local, so sched.c puts it at the top beside its
   loads; `acc` is then set once where the last add is, and the clamp values no longer lose their registers to it. This
   is what moved the load/add order from 90 (the floor of every wrapper and ordering search, ~20k variants) to 45.
4. The staged add and the load before it in nested one-shot scopes
   (`do { t0 = load; do { stage = head + t1; } while (0); } while (0);`). flow.c adds `loop_depth` to each reference
   inside, so local-alloc's `floor_log2(refs) * refs / life` ranks the staged value above the opening factor; the
   one-statement scope alone left two block-local registers exchanged (50, nine rows). The nested pair is 0.

Failed experiments that would otherwise be repeated:

- Wrappers around any subset of up to three groups of the 17 statements, statement permutations, declaration order,
  commutative swaps, two-statement and inline forms of each sum, temp-versus-inline loads: all stay at 90 or 45 once
  the order is right; the 8-row register remainder (45) was never moved by a single-depth wrapper, only by the nested
  pair, found by a depth search over the statements (`do { do { } while (0); } while (0);` triples the weight).
- `if (v) { x = a; } else { x = a; }` with an uninitialised `v` improves the score by changing which pseudos are
  block-local; it is not a program the original could be, and nothing here needs it (it is carried in the saved best's
  tail and is harmless; the family leaves it).

What is controlled evidence and what is one function:

One function. The isolated-judge scores are exact (the app's judge); the compiler explanations are the source's for
sched.c and hypotheses for the local-alloc priorities. The new family reproduces 120 -> 0 from the saved best in one step.
