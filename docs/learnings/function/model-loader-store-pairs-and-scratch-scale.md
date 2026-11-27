---
title: model loader store pairs and scratch scale
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 230-instruction N64 model loader: it reads a magic byte and a header, then walks nodes, groups and triangles and
rescales or shifts two halfword texture coordinates per vertex. Per-function `-mfp32` (the project default is
`-mfp64`). The m2c draft scored 10574; a typed rewrite from the assembly scored 3710.

The source change, and the measured result before and after (scores, the judge used):

Judged with the app's standalone judge. Each "reverted" score is that one step undone from the exact source.

- Both halves of `field = x; field <<= 6;` pairs are stored when written
  `a = x; s = a; b = y; t = b; s = a << 6; t = b << 6;` (reverted: 780). Family `shift_store_interleave`.
- A scratch `int` holds the magic byte test and later `off = count; off <<= 4;` (reverted: 20). Family
  `scale_scratch_reuse`.
- A parameterless helper is passed three just-computed pointers, which fixes `$a1`/`$a2` (reverted: 40). Family
  `helper_live_arguments`; scaffolding.
- The three-vertex check loop walks a pointer beside its counter (reverted: 500). Family `element_member_walk`.
- The triangle step is at the head of both arms (reverted: 460). Family `step_into_both_arms`.
- The empty-model arm reads the local, not the field just stored from it (reverted: 200). Family
  `stored_field_local_value`.
- A group's index count has its own local, not the inner loop counter (reverted: 25). The library already splits it.

The exact source also keeps the entry pointer in the inner loop counter (scaffolding).

Failed experiments that would otherwise be repeated:

- Address spellings of the two header pointers (struct arrays, `&hdr[i]`, int sums, inline helpers, read-backs of the
  stored field) all left `andi $v0` or moved the pointer's `addu`.
- `do { } while (0)` around one of the two address computations reached 60 but never the target order.
- A halfword local for the count delays the count's store or loses the `andi`.

What is controlled evidence and what is one function:

All of it is one function. That the flow pass deletes the first store of each pair, and that a two-block local is
left to global.c after local-alloc, are hypotheses from the dumps' behaviour, not read in the compiler source.
