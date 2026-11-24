---
title: A 2,267-instruction collision sweep: m2c's forward goto arms as else arms, the plane-behind test first, and the result pointer read once (24406 -> 6792)
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

Continued 13655 -> 6792 (same judge, each confirmed with `compile_and_diff`):

6. m2c's `s16 sp1E6 = w & 0xFF00;` as `u16`: 13655 -> 12757. `unsigned_narrow_local` stood down for the whole
   function because the target has an `lh` elsewhere; a local that only holds a mask past its sign bit is now offered
   anyway (decomp-repair 0.5.941).
7. Each `segmentClosestPoint(&edge, a, b, p); d = distanceSquared3D(&edge, p);` pair written through one pointer local
   shared by all eight pairs (`pt = &edge; segmentClosestPoint(pt, ...); d = distanceSquared3D(pt, p);`): 12757 ->
   7266, frame 0x2A0 -> 0x290. The target computes each address once into `$s0` for both calls; one local per pair
   was worse (13360). decomp-repair `call_pair_address_share` (0.5.943).
8. The clamp's identical-arm scaffold `if (p) { t = y + a7; } else { t = y + a7; }` as `do { t = y + a7; } while (0);`:
   7266 -> 7009 and the float argument back in `$f30` (collapsed plainly: 8383, the argument loses its register).
   decomp-repair `identical_arm_scope` (0.5.946).
9. The `spXX` locals declared in slot order: 7009 -> 6792. `spill_name_order` already offers it.

Also tried at 6792: `sp194` not volatile (7792, frame 0x298); the retry walk as
`for (; p != NULL; p = p->next)` with the miss arm `continue`-ing it (7117); the clamp's statement orders without
the scaffold (8383-8678); the hit arms' flag stores reordered (7466 / 7266).

What is controlled evidence and what is one function:

Steps 1-9 are one function, each measured alone against the state before it. Left at 6792: the frame (0x290 vs
0x288) and register roles everywhere (`alloc_roles.py` replays only 62 of 165 allocnos here, so its what-ifs are not
reliable on this function); the object/mesh pointers swap `$s5`/`$s6`, the three vertex pointers rotate `$s1-$s3`.
