---
title: drop shadow element index and member order
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A 773-instruction N64 drop-shadow renderer (SN64 GCC 2.8.1, the per-function `-mfp32` profile its matched neighbours use).
It walks a triangle list, builds three `Vtx` per triangle in a global buffer `VERTS[buf][base + k]` and emits display-list
commands. The saved best (a hand rewrite, 2566 under `-mfp32`, 9820 under the project's `-mfp64`) had the vertex written
through a pointer local, `vt = &VERTS[buf][base + k]; vt->ob[0] = ..; vt->flag = 0; ..`, a frame 8 bytes larger than the
target's and `%hi` of the buffer index global kept in a saved register over the whole outer loop.

The source change, and the measured result before and after (scores, the judge used):

Measured with the app's judge-worker (`fast_judge.py` with the flags replaced by the `-mfp32` profile), then an exact
`compiler_experiments` trial applied with `suggest`:
1. Every vertex member written through the indexed element (`VERTS[buf][base + k].ob[0] = ..`): 2566 -> 1080. The
   pointer was one long-lived address; written through the element, each member store re-forms the address and the
   buffer's `%hi` stays in the inner loop (the target re-forms it before the loop and again for the next command), so
   the extra saved register and its spill go away. decomp-repair `element_pointer_direct_index`, extended to several
   subscripts of names, literals and `+ - *` (PR 1731).
2. The loop's entry pointer `ent = &TRIS[i]` written the same way: 1080 -> 1020 (the family already handled it).
3. The ten member stores in the struct's declaration order (`ob[0..2]`, `flag`, `tc[0..1]`, `cn[0..3]`) instead of
   m2c's instruction order: 1020 -> 0 (an exhaustive grid of the three groups' orders, 288 compiles, found the same order
   best). New decomp-repair family `member_store_declaration_order` (PR 1731); `prove_chain` from the saved best now
   reaches 0 with the two families.

Failed experiments that would otherwise be repeated:

op83's note on this function lists 1-D indexing and a struct-wrapped row for the vertex address order: both lose to the
per-member indexed element.

What is controlled evidence and what is one function:

One function. The family's children were judged on the saved best and the match is recorded in the app.
