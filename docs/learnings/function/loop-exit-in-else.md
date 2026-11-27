---
title: A loop whose exit is the else of its test keeps the test at the top
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: an 84-instruction N64 function with the same
counted loop in two arms (format a string, print it, load it). The target tests `andi; sltu; beqz` at the head of each
loop and ends it with `j head; addiu`, with the string addresses and the byte limit hoisted above the loop.

The source change, and the measured result before and after (scores, the judge used): the app's judge.
`while (1) { if (i < count) { ...; i++; } else break; }` is exact; the same loop as `for (;;) { if (i >= count) break;
... }` or `for (; i < count; i++)` scored 3170. From the m2c draft (1160): both loops written that way with the counter
set at the head of each arm 45 (`loop_else_break_arms`), the counter declared `u16` with its masks dropped 20
(`masked_reads_narrow`), the loop counter passed as `sprintf`'s fourth argument at both calls 0
(`missing_call_argument_local`, which now gives every short call the value at once). The counter was a dead m2c local
named `temp_a3`: the target computes `andi $a3` before the compare and never writes `$a3` again before the call.

Failed experiments that would otherwise be repeated: with one `var_s0 = 0;` before the outer `if`, 795. The argument
at one call only scored worse than at none or both. `s32` limit parameter masked at the test was also exact, so the
`u8` parameter is a choice, not evidence.

What is controlled evidence and what is one function: one function. That GCC's loop rolling does not recognise a
conditional jump to an `else` label as the loop's exit is a hypothesis for SN's build (no source).
