---
title: operand access helpers as one child
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like and the exact compiler and flags:

A 148-instruction script-op handler (KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): two operands read and a result
written back, each access "flag: call, else masked index into a 16-entry file or a global table", the result clamped to
an s16. Saved best 1135 (m2c's ladders, a `u8` carrier of the opcode, the clamp in a narrow local).

The source change, and the measured result before and after (scores, the exact judge):

Four edits that are worse or equal alone and exact together, 1135 -> 0: (1) each read a `static __inline__` helper
`(p, u8 sel, u8 idx)` called with the site's own flag and index expressions; (2) the store half a helper too, fed two
`u32` locals made first, the flag unmasked (the mask belongs to the u8 parameter; locals keep the two shifts in separate
pseudos so the inline's parameter copy survives next to its source, expressions are folded by cse); (3) the opcode
carrier local removed (`(param & 0xFF)` in the compare and the switch); (4) the clamp written `if (r >= min) { n = max;
if (r <= max) n = r; } else n = min;`, which makes reorg fill the first branch the way the target has it.
Alone, with the rest as m2c wrote it: helpers only 1580, plus the write 1185, plus the carrier 320, plus the polarity
instead of the carrier 865. The library family is `operand_access_helpers` (decomp-repair): it reads m2c's ladders and
offers the combination as one child.

Failed experiments that would otherwise be repeated:

Passing the write's flag and index as expressions: 1830. Masking the flag local: 654 (the load folds into `lbu`/`lhu`).
Clamp inside the helper on its parameter: the parameter copy and the compare share a pseudo and the target's separate
result register never appears.

What is controlled evidence and what is one function:

One function, exact judge; the family reproduces the match from the saved best in one step (prove_chain).
