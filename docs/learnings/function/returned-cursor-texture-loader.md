---
title: texture loader that takes and returns its display-list cursor
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 430-instruction N64 leaf that loads a
4-bit, 8-bit or 16-bit texture, chosen by a depth byte, into the display list it is given and returns the cursor.
Wrap masks come from two switches on the width and height bytes. Compiler as above; the target's `jr ra` carries the
stack adjustment and the jump tables are loaded with `la` and `addu`.

The source change, and the measured result before and after (scores, the judge used): the app's judge. The m2c draft
was 28740 under SN GCC 2.7.2. A typed rewrite from the assembly with `Gfx *gfx` and `gfx++`, the SDK's
`gDPLoadTLUT_pal16`, `gDPLoadTextureBlock_4b` and `gDPLoadTextureBlock` shapes (both clamps and dxt ternaries from the
macros) scored 3255 at once. Then: the switches' first label is `case 0:`, read from the jump table (65 entries from
0), and the palette byte read into a local before the switches (560). The same source under SN GCC 2.8.1 `-mfp32` was
685 with a `u8` palette local (an extra `andi`) and 0 with `s32 palette`. Under 2.7.2 the `s32` form stays 560
(`bne` for `bnel`, and the `jr ra` delay slot not filled), so the profile was part of the match.

Failed experiments that would otherwise be repeated: KMC 2.7.2 with the project's default flags is far off (9167).
decomp-repair `narrow_widening` already writes the `u8` to `s32` step. `gbi_value_cursor` (decomp-repair, added after
this match) writes the draft's runs as macros (28740 to 26516 under 2.7.2) but leaves the draft's computed words ahead
of the commands.

What is controlled evidence and what is one function: one function.
