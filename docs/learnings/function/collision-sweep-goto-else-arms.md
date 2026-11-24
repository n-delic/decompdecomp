---
title: A 2,267-instruction collision sweep: m2c's forward goto arms as else arms, the plane-behind test first, and the result pointer read once (24406 -> 13655)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

An N64 collision sweep (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, 2,267 instructions, frame 0x288): a list walk
over actors, then per-mesh triangle loops (u16 and u8 index variants, written twice each), each triangle tested by
plane distance, `triangleContainsPoint`, and else a closest-edge push-out; a result-pointer epilogue and a retry walk.
The saved best was m2c's draft tuned by searches (24406, scoring version 2).

The source change, and the measured result before and after (scores, the judge used):

All scores from the app's standalone judge (`fast_judge.py`), each new best confirmed with `compile_and_diff`.

1. m2c's `if (tri(...) != 0) { HIT; goto block_73; } DIST ... { ...; block_73: arg3[2] = v; }` (two sites) written as
   `if (tri(...) == 0) { DIST ... { ...; arg3[2] = v; } } else { HIT; arg3[2] = v; }`: 24406 -> 22230 (one site),
   18762 (both). The target's `bnel $v0, $zero, <hit>` with the hit block after the push-out path is the evidence:
   GCC 2.7.2 emits the then-arm in line and the else-arm after it; cross-jumping merges the two copies of the final
   store again (one `sb` lands in the `j`'s delay slot). decomp-repair `goto_label_else_arm` (0.5.938).
2. `if (!(d < 0.0f)) { PUSH; goto next; } if (dot > 0.0f) {...} goto next;` written with the plane-behind test first,
   `if (d < 0.0f) { if (dot > 0.0f) {...} goto next; } PUSH; goto next;` (two sites): 18762 -> 15070. The target's
   `bc1fl` over the dot test into the push code shows the order. decomp-repair `branch_polarity` already offers each
   site (16660 / 17172 alone).
3. The search's base pointer `__base_gPlayerActor = &gPlayerActor; *__base_gPlayerActor ...` written as the global:
   15070 -> 14705 (`symbol_base_unwrap`, new).
4. The result pointer read once in its tested block (`actor = gPlayerActor; if (actor != NULL) { switch (actor->kind)
   ...; *out = actor; actor->flags |= f; ... }`): 14705 -> 13685. `global_pointer_local` refused it because the
   global is stored elsewhere in the function and the block holds a `switch`; it now holds the local inside the
   tested block only.
5. The retry walk at the end as a real `do { } while (next != NULL)` with the sphere test's miss arm first: 13685 ->
   13655 (by hand; the `return (s8) flag` inside it is hoisted to the inner loop's preheader in the target).

Failed experiments that would otherwise be repeated:

- Copying the label's run into the hit arm without inverting (`goto_arm_label_copy`'s shape): 24696 (worse).
- `sp194` (the first parameter) not `volatile`: frame 0x2a8 and 13739; with it, 0x2a0. The target spills it too
  (`lw $t1, 0x194($sp)` read once for two field loads), so the remaining 24 bytes of frame are more spilled pseudos
  in ours, not this local.
- Dropping the unused `volatile char pad;` local: 13660, frame unchanged.
- An outer `for (;;)` around the retry walk so the miss arm continues it: 13835.

What is controlled evidence and what is one function:

Steps 1-4 are one function, each measured alone against the state before it. Left at 13655: the frame (0x2a0 vs
0x288) and register roles everywhere (`alloc_roles.py` replays only 62 of 165 allocnos here, so its what-ifs are not
reliable on this function); the object/mesh pointers swap `$s5`/`$s6`, the three vertex pointers rotate `$s1-$s3`.
