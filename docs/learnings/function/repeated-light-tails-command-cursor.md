---
title: repeated command tails with a scrambled global cursor, 14500 to 0
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

A 470-instruction N64 scene light setter: an `if`/`else if` over a page number, each arm storing light colours
through a reloaded global pointer and ending in the same three display-list commands, built with the profile above.
m2c's draft keeps the cursor as `extern void *`, repeats the tail seven times and, where the target uses a register that
still holds an earlier value of the cursor, prints the global again.

The source change, measured with the app's judge: every tail written as
`{ CursorCommand *cmd = gCursor++; cmd->w0 = ..; cmd->w1 = ..; }` per command, the global declared as a pointer to the
two-word record. The m2c draft scored 14500; with only that change it is 0. The light stores, the `void *` pointer they
go through and m2c's early `return`s did not matter. The decomp-repair family `asm_cursor_command_tails` writes this one
child from the target's own stores (draft to 0 with `prove_chain.py --families asm_cursor_command_tails`).

Failed experiments that would otherwise be repeated: none. Retyping the light stores was not needed.

What is controlled evidence and what is one function: one function. The typed cursor and the tail shape were changed
together and were not isolated from each other.
