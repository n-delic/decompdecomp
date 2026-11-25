---
title: A late reader of a decremented counter, moved above the decrement, frees the first register
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mfp64
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

The same 477-instruction texture-strip blitter (SN GCC 2.8.1, `-G0 -mips3 -O2 -mfp64`). After `n--;` a SetTileSize word
read `(n << 2) & 0xFFF` near the top of the block and `offset += texw * (n << 2);` read it at the bottom. The target has the
shift in place, `sll $a2, $a2, 2`; ours put it in `$t1` and shifted five cursor temporaries one register up.

Why: `n << 2` is a block-local quantity; local-alloc.c ranks quantities by `floor_log2(refs) * refs / (death - birth)` in
the first scheduler's order, and with the accumulator update last the product (and so the shift's death) is scheduled at
the end of the block: the shift lived 23 insns against the cursor temporaries' 16-20 and was allocated last.

The source change, and the measured result before and after (scores, the judge used):

`offset += texw * ((n - 1) << 2);` written above `n--;` (or `dtdy = ((n - 1) << 12) / strip;` moved there instead): 435 -> 370
(13 register rows). decomp-repair `decrement_use_hoist` (#1743, 0.6.37).

Failed experiments that would otherwise be repeated:

Every placement of the update after the decrement (five positions), `(n << 2) * texw`, `texw * 4 * n`, `((n << 2) << 10)`
for the division: all 435 (invariant). `n <<= 2;` in place: 1090 (the counter then lives through the block and the
locals take `$a2`/`$a3`).

What is controlled evidence and what is one function:

One function; the life lengths are read from the `.lreg` dump ("used 6 times across 23 insns").
