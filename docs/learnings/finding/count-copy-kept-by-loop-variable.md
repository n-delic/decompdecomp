---
title: A count read through a copy the compiler keeps: the copy is a variable the later loop uses
kind: finding
compiler: SN64 GCC 2.8.1 -G8 -mips3 -O2 -mfp32
evidence: verified
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:
A 141-instruction N64 animation-player init (`sn-n64-281`, per-function profile `-G8 -mips3 -O2 -mgas -meb
-mcpu=VR4300 -mhard-float -mfp32`). The target loads the count (`lh $v1, 0xC($a1)`), stores it, copies it
(`addu $a1, $v1, $zero`) and builds the allocation size `n * 36 + n * 36 + n * 12 + ...` from the copy; a joint
loop follows.

The source change, and the measured result before and after (scores, the judge used):
150 (default profile) / 110 (`-mfp32`) -> 0: `m = n;` before the size, the size read through `m`, and `m` reused as
the joint loop's byte offset (`i = 0; if (i < p->count) { one = D_800076B8; m = 0; do { ...
((Joint *)(m + (s32) p->anim->joints))->f1C ...; i++; m += sizeof(Joint); } while (i < p->count); }`). Reusing the
loop counter for the copy instead: 110 -> 30 (the copy then in the counter's register). The offset written first in
an int sum gives the target's `addu $v0, $a1, $v0`; the loop's float constant set into a local at the guard's head
keeps the target's preheader order (its load before the offset's `move`). Applied with an exact `compiler_experiments`
trial and `suggest`.

The rule (the `-ds`/`-dl` dumps): cse rewrites a copy's uses to its class's first register; a new register becomes
the first (`make_regs_eqv`) only when it lives outside the current cse block and longer. do-while(0) fences split
cse1's blocks, but cse2 sees one block up to the loop and deletes the copy (`m` gone from `.cse2`). local-alloc ties a
surviving copy to its dying source (`combine_regs`) unless the destination is not block-local. A variable the later
loop uses lives across blocks: cse2 keeps it as the head and global.c allocates it apart from the count.

decomp-repair `loop_offset_copy_reuse` (0.6.21) offers the counter copy and the offset copy.

Failed experiments that would otherwise be repeated:
A plain copy in every placement (in or out of fences, with the store before or after, a size local, a static inline
helper, `p->count` reads): no `move` at all, 110-1145. Type grids (s16/u16/s32), 121 fence placements.

What is controlled evidence and what is one function:
One function. The cse/local-alloc rule is read from the 2.8.1 source and the dumps.
