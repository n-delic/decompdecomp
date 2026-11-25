---
title: m2c block move loops are one struct assignment
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

An 84-instruction save-data routine of an N64 game, SN GCC 2.8.1 `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float
-mfp64`. After a clear, a read and a check, it copies a 0x21C-byte buffer into a second one. The target is GCC's
block move: `or`/`andi 3` of the two addresses, a loop of four `lwl`/`lwr` + `swl`/`swr` words, `j` over a second loop
of four aligned `lw`/`sw` words, both stepping 0x10 to an end pointer 0x210 past the source, then three unaligned tail
words. m2c's draft spells it out as two pointer locals, an end pointer, `if (((s32) &A | (s32) &B) & 3)` with one
`do`/`while` per arm (`M2C_UNALIGNED32` in the first) and the tail stores. Saved best: the raw draft, 9473.

The source change, and the measured result before and after (scores, the judge used):

- `typedef struct { u8 data[0x21C]; } Block;` and the whole run as `*(Block *)B = *(Block *)A;`: 9473 -> 0
  (fast_judge, then `compile_and_diff`). The size is the end pointer's offset plus the tail's last byte (0x210 + 0xC).
- The byte record is what makes the compiler test alignment at run time: the same assignment with
  `s32 data[0x87]` (word alignment) drops the test and the `lwl`/`lwr` loop (4500).
- decomp-repair `block_move_struct_assign` makes the rewrite from the raw draft in one child (byte record first when
  the draft has the test, word record first when it has one aligned loop).

Failed experiments that would otherwise be repeated:

- None needed: the draft's loops are the compiler's own expansion (`movstrsi_internal`), so respelling them is not a
  search worth starting.

What is controlled evidence and what is one function:

One function, exact in the app's judge. The mapping "alignment test present -> record of bytes, absent -> record of
words" follows from GCC's block-move expansion and is the family's first-child rule, not separately measured.
