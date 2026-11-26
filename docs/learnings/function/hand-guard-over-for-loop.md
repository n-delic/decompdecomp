---
title: hand guard over for loop
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---
What the function (or case) looked like and the exact compiler and flags: a 45-instruction table search returning a
`u8` index, KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O1` (the project default `-O2` was wrong for this function). The
target has one `beqz` guard with `move $a0,$zero` in its slot, then the two key loads and a second read of the count,
then a bottom-tested loop: that is the compiler's own copy of a `for` loop's entry test followed by loop.c's
invariant loads (the second part is a hypothesis from the output, not read in the source).

The source change, and the measured result before and after (scores, the judge used): earlier attempts wrote the guard
and the key locals by hand around a `for`. `for (i = 0; i < p->count; i++)` with the globals read in the body is exact
(app judge). From the saved best: 350 -> 0 with decomp-repair `guard_over_for`.

Failed experiments that would otherwise be repeated: dropping the guard but keeping the key locals before the loop
(390); inlining the keys but keeping the guard (350).

What is controlled evidence and what is one function: one function; the library tests cover the rewrite only.
