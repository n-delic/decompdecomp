---
title: A load through a global pointer sat one slot late until it was read as a struct member (60 -> 0)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-22
---

What the function looked like and the exact compiler and flags: an N64 game's matrix-emit function (95 instructions, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`). After a store of `sMatrixPoolUsed` (a scalar global) it read `sRenderState + 0x74C` (a pointer held in another global) into a saved register. Saved best 60: every instruction right, only two rows `reordered` (the target has `addiu $v0,$a3,1` before `lw $s0,0x74c($t0)`, the candidate the reverse).

Why (read in the compiler's `sched.c`, source in the toolchains folder): the first scheduler runs backward. A load written `*(s32 *)((char *)base + 0x74C)` is not a MEM_IN_STRUCT reference, so `true_dependence`/`anti_dependence` tie it to the earlier store to `sMatrixPoolUsed`; it only becomes ready after that store is launched, and the store's memory-unit blockage then blocked it for two cycles (`blocking insn 57 for 2 cycles` in `-dS`), so the independent `addiu` took its slot and the two ended in the other order. Written as a member of a one-member record, `((struct { int field_74C; } *)sRenderState)->field_74C`, the reference is MEM_IN_STRUCT at a varying address and a scalar global at a fixed address cannot conflict with it (the comment above `read_dependence`): no dependence, no blockage, the target's order. Removing the store altogether gave the same order, which located the cause.

The source change, and the measured result before and after: both reads of `+0x74C` as members of a record with a 0x74C-byte pad. 60 -> 0 in the app's exact judge (recorded as matched). One edit; no scaffolding.

Failed experiments that would otherwise be repeated: statement orders of the three-line increment/sum/store block (a grid of 12: best 60, others 240 to 840), `++`, `+= 1`, a separate shift local, the sum with its operands swapped (465: different allocation), a local for the fourth argument at four positions, `register` removed, field types changed, moving the store into the call's argument list (60: the store still launched next to the load), and all 107 children of the library's families (none better than 60) - `field_struct_view_printed` was the family for it, but its pattern only read m2c's `(((char *) base) + off)` spacing and this source spelled `*(s32 *)((char *)(sRenderState) + 0x74C)`. decomp-repair PR 1227 reads that spelling.

What is controlled evidence and what is one function: the compiler rule (member references do not alias a scalar global's store in `sched.c`) is read from the source and matches an earlier camera-function result; the 60 -> 0 is one function, measured with the exact judge on the saved best and on the family's child.
