---
title: a byte string walked by a pointer: written as the index loop, the pointer start moves after the jump table address
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

An 82-instruction font routine (measures a string: a `switch` over the byte with a jump table, skip-a-byte arms, a wrap
width read from the byte after an escape, a callee given the pointer to the current byte) sat at 360 in the app's
judge. The rows were three: the loop's preheader had `move $s0,$s4` first and then `lui/addiu $s7,jtbl`, and the
target has the jump table's address first and the move after it. The two operand rows were only a consequence: the
linker places the jump table where the ROM's code points, by the instruction's offset, so one instruction out of place
gave "your code refers to .rodata+0x0 where the ROM's code has no address".

| edit | score |
| --- | --- |
| saved best (`char *cursor = text; do { ...; cursor++ } while (cursor < (char *)((u32)length + (u32)text))`) | 360 |
| the end address in a local before the loop | 560 |
| `cursor = text` before the `if (length != 0)` | 935 |
| `u32 i`, `symbol = text[i]`, `i++` in the skip arms and at the bottom, `text + i` to the callee, `while (i < length)` | 0 |

No other edit and no scaffolding: it is the program a developer writes.

Mechanism (hypothesis from the dump of the scheduling order and sched.c's `rank_for_schedule`, not confirmed by a
stage-by-stage diff): with all latencies 1 the first scheduler keeps the original order, so the order in the preheader is
the order loop.c emitted. A pointer the source walks is an ordinary pseudo, set before the loop note, so its `move`
comes first and the hoisted invariants follow. A pointer made from an index is a derived induction variable that
loop.c itself initialises when it strength-reduces, after the hoisted invariants (`la` of the table) and before the
loop-end compare's address (`addu $s6,$a1,$s0`): `la, move, addu`.

Failed on the way: the end address in a local; the pointer set above the guard.

decomprepair `string_walk_index` (0.5.653) offers this rewrite (a pointer set to a base and stepped to base plus a
length, `do`/`while` or `for`): from the saved best its single child is the exact one. `loop_pointer_to_index` needs a
counter beside the pointer, so none of the 110 children the existing families gave from the saved best improved it.
One function; the family's bail-outs (pointer used after the loop, stepped in an expression, address taken) are tested.
