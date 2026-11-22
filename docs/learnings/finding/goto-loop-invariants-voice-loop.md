---
title: goto loop invariants voice loop
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function looked like and the exact compiler and flags:

An 89-instruction N64 function walking a table of voice slots (a lock, a handle resolved through a call, then a loop that
sends a 14-bit value as two byte parameters with two calls per slot). gcc-2.7.2-kmc, -G0 -mips3 -mgp32 -mfp32 -O2. The
target keeps `high = (v & 0xffff) >> 7` and `low = v & 0x7f` in saved registers hoisted before the loop and still masks
`low` with `andi $a3,$sN,0xff` at both calls. The saved best (m2c's `again: ... if (c) goto again;` with the two values
cached in locals) sat at 45: the two saved registers swapped, and a cached `low` loses its mask.

The source change, and the measured result before and after (scores, the judge used):

The app's judge, one set of edits from that best: (1) the label and backward goto as `do { } while (c);`; (2) the cached
locals dropped and written at their uses (loop.c hoists them again into fresh pseudos); (3) the second `index * 0x1a0`
(the final table address) through a fresh local; (4) the callee's last parameter declared `u8` and the `& 0xff` on each
argument dropped; (5) the one-shot `do { } while (0)` around a call removed. Measured: do/while with inlined values and
masks kept 1441; plus the product local 910; plus the `u8` parameter 25; plus unwrapping the fence 0. decomp-repair
`goto_loop_invariants` (0.5.621) makes the whole set one child: the saved best 45 reaches 0 from its first child.

Failed experiments that would otherwise be repeated:

Keeping the cached locals and the goto form: every spelling of `low` (`u8`, `s32`, `low = p; low &= 0x7f;`, a `(u8)`
cast, a `u8` prototype) folded the `andi 0xff` away (hypothesis: combine.c knows the bits of a one-set pseudo; a second
set whose copy survives to combine kept the mask but cost a `move`). A second live set, uninitialised reads, and
conditionally set locals only added instructions. A natural while/do-while loop with scalar symbols and
`(char *)&sym + off` hoisted the table's address (`lui/addiu`) as a loop invariant; the offset must be an expression the loop
pass cannot treat as a constant address. Initialising the offset local from the parameter (`off = original;`) also
removed a stray copy in one variant.

What is controlled evidence and what is one function:

One function, scored with the app's judge; the compiler reasons are hypotheses read from output, not from its source.
