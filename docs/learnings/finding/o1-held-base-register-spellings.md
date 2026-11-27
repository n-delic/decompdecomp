---
title: At -O1, a global address held in a register comes from pointer arithmetic or from two members of one block
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: N64 functions of 606 and 1167 instructions,
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, whose targets build a global's address in a register where the plain C
spelling stores through `lui $at`.

The source change, and the measured result before and after (scores, the judge used):

- `slots[i].a = x; slots[i].b = f(...);` stores through the symbol; `(&slots[i])->a = x; (&slots[i])->b = ...;` holds
  `slots` in a register and stores `b` as `1(base + i * 5)`: 1890 to 1082 on the 1167-instruction function.
- Two halfword stores 0x28 apart as `lui; addiu` then `sh 0(r)`, `sh 0x28(r)`: two members of one record declared at
  the block's symbol (both at offsets other than 0 from it) gave it, 1082 to 0 together with the frame size. A pointer
  local `p[0]`, `p[0x14]` was folded back to two `lui $at` stores.
- `p = &pair[1]; ... q = p - 1; *q += 2.0f;` keeps `-4($v0)`; `p[-1] += 2.0f` is folded to the symbol: 800 to 0 on the
  606-instruction function.
- A sine call whose destination address the target computes before the `jal` was the statement expression
  decomp-repair's `call_statement_expression` already proposes (2811 to 805).

Failed experiments that would otherwise be repeated: a record whose first accessed member is at offset 0 stores
directly through the symbol; `<< 1` for `* 2` on the converted byte was worse.

What is controlled evidence and what is one function: each line is one function; scores are the app's judge run
isolated, and both functions were then recorded exact by the app.
