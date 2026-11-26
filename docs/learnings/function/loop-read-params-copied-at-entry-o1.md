---
title: Parameters the loops read, copied into locals at entry together
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 134-instruction string drawing routine with a wrapping loop and an eleven-argument call, gcc-2.7.2-kmc
`-G0 -mips3 -mgp32 -mfp32 -O1`. The target loads the stack string pointer before it moves two register parameters into
saved registers, and reads a byte argument with `lbu` of the word's low byte and `andi` before the loop.

The source change, and the measured result before and after (scores, the judge used):
Plain C was 65. Locals assigned from the start x, the x step and the byte argument (an `s32` parameter into a `u8`
local) at the top: 5. The last rows, `addu step,cur` in that operand order, were 0 with the accumulate written into a
temporary (`next = step + cur; cur = next;`, `accumulate_separate_dest`). The library family `loop_params_entry_copy`
writes the three copies together.

Failed experiments that would otherwise be repeated:
Any one copy or any pair stayed at 65 or went to 220. `curX = dx + curX`, `u32` and cast spellings of the sum did not
change the operand order. `-O2` and `-O1` with single scheduling or strength-reduction flags were far worse (1100+).

What is controlled evidence and what is one function:
One function; the pass that orders the parameter loads was not read.
