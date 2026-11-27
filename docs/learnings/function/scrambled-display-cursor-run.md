---
title: A draft's display-list run can store a command word one command too far
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 61-instruction N64 light setup: 16 byte stores through a global record pointer, then three display-list commands
through a global cursor. m2c's draft held the cursor in locals and, after storing an advanced cursor, printed the global
again where the target uses a register with the earlier value: one command's first word was written one command too far
and another command was left short. Saved best 845. `gbi_macro_commands` wrote no child.

The source change, and the measured result before and after (scores, the judge used):
The three commands, read from the target's stores, written as `{ Cmd *cmd = gCursor++; cmd->w0 = ..; cmd->w1 = ..; }`
on a two-word record: 845 -> 20 (app judge). Then the two adjacent byte stores the draft kept in the scheduler's order
swapped (`independent_statement_swap`): 20 -> 0. decomp-repair `asm_cursor_commands` writes the first step.
A typed from-scratch rewrite (record pointer, `->` members, the same three blocks) was also exact on its first compile.

Failed experiments that would otherwise be repeated:
The same cursor advanced as `gCursor = (Cmd *)gCursor + 1;` after a separate read, on a `void *` global: 890. The swap
alone on the saved best: 830. The saved record pointer retyped alone: 845.

What is controlled evidence and what is one function:
One function. That the draft's bad address comes from m2c re-printing the global after its write is a hypothesis read
from the draft against the assembly, not from m2c's source.
