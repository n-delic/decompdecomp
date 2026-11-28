---
title: global index staged before pointer
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 184-instruction N64 model-drawing function: a position matrix command, a loop that projects each vertex, a loop that
appends the vertices to a vertex buffer at a global count (advanced inside the loop), one vertex command and a loop of
line commands. Matched under the profile above.

The source change, and the measured result before and after (scores, the judge used), with the app's judge:

- m2c draft (saved best): 7797. A rewrite by hand with typed records and walked vertex pointers: 570.
- Loops that the target tests at the top and closes with `j` were written `while (1) { if (i < n) { ...; i++; } else
  break; }`; a plain `for` was rotated. The two pointer compares are joined with `|` (`xor; sltiu; or`).
- The vertex command word with the opcode first (`0x01000000 | ((u32) (n & 0xFF) << 12) | ...`): 15 differing positions
  -> 2. decomprepair `or_constant_lead` and `or_chain_order` offer this.
- Last 2 rows, the order of two `lui`s: `vtx = &gVerts[gCount];` -> `s32 base = gCount; vtx = &gVerts[base];`, 20 -> 0.
  Also exact as `base + gVerts`. New family: decomprepair `global_index_staged`.

Failed experiments that would otherwise be repeated:

- `vtx = gCount + gVerts;` alone left the two loads in the same order.
- `(Vtx *) (gCount * 0x10 + (u32) gVerts)` fixed the loads but swapped the operands of the `addu`.
- `operand_temporary` never offered the staging: it skips a global the function writes and does not look in subscripts.

What is controlled evidence and what is one function:

One function. The load-order effect was measured on two spellings of the same source.
