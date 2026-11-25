---
title: A record written through `TABLE[count].field`, not a pointer local, puts the symbol's address before the index load
kind: finding
compiler: sn-n64-281 (GCC 2.8.1) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 920-instruction N64 ray/triangle test that appends each hit to a global record array (`D_80148CF8[D_800B76A4]`,
0x38-byte records: kind, height, normal, t, world, hit, object, triangle), everything inline (sqrtf builtin, vector
math written per component). The target is built `-mfp32` (`mtc1 $zero,$fsN` / `$fsNf` pairs for the 0.0 doubles);
the function's recorded profile was `-mfp64`, under which no source comes near (fp64 20544 against fp32 7 for the
same source).

The source change, and the measured result before and after (scores, the judge used):

`ent = &D_80148CF8[D_800B76A4]; ent->kind = 1; ...` (fourteen member stores) written as
`D_80148CF8[D_800B76A4].kind = 1; ...`: 352 -> 11 in the app's judge-worker. The target computes the symbol's
`lui`/`addiu` first (the `lui` even fills the delay slot of the sqrt fallback branch before it) and loads the index into
`$a0`; with the pointer local the index is loaded first into `$v0`, and no statement order moved it. decomp-repair
`element_pointer_direct_index` offers the rewrite.

Other steps on the same function, each measured: one `len` local per sqrt site instead of one shared (1594 from 2544: a
shared one is a global pseudo and the sqrt result is copied into it); a pointer local per use site for the record
pointer at the end (`e`) rather than one reused in the loop (2544 from 4832); the end block's pointer copies before the
sqrt call (632 from 1312); division statements written x, y, z where the compiler emits x, z, y.

Failed experiments that would otherwise be repeated:

The last 7 points are stack slots: `sign` (set to -1.0f, then 1.0f) at 0x15C in ours and 0x168 in the target, after the
loop-made `&tv` slot and an unused 8-byte slot, which says the target's `sign` got a hard register in global.c and was
evicted when reload spilled `$f22`. Declaration order (every position, 160 random orders), ternary/if-else forms, the
multiplication's operand order, one-shot weights (7400+, scheduling), and prove_chain over all 1782 children did not
move it.

What is controlled evidence and what is one function:

One function; the pointer-versus-index effect is measured, its cause (cse building the address from the symbol) is a
hypothesis from the output, not traced in cse.c.
