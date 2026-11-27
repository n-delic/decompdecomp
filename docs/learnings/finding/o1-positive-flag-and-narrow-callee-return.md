---
title: o1 positive flag and narrow callee return
kind: finding
compiler: gcc-2.7.2-kmc -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

N64 object-update routines of 345 to 408 instructions (`-G0 -mips3 -mgp32 -mfp32 -O1`): a loop over seven slot records
setting a local 0/1 flag, then an `if` on the flag and a counter test, then a call with 18 arguments (nine stack floats).

The source change, and the measured result before and after (scores, the judge used):

Judged with `fast_judge.py` against the app's judge-worker; the final sources are exact in the app.

- `if (found & ((count & 0x3F) == 0))` compiled to a branch and `andi found, 1`; `found > 0 & (...)` gave the target's
  `sltiu` then `and` (410 -> 10). `found != 0 & (...)` where the target has `sltu` then `and` in the other order
  (`(count & 0x1F) == 0 & found != 0`). decomp-repair `flag_positive_and`.
- A callee declared `u16` (not `s32`) whose result goes to a `u16` local gave `move $s1, $v0` then `addiu $v0, $s1, 2`
  instead of `addiu $v0, $v0, 2` (10 -> 0). decomp-repair `callee_narrow_return`.
- A product that the target computes before loading a float constant used in a later subtraction was a separate local:
  `f = n * K; z = 1584.0f; x = z - f;` (115 -> 0 together with `px`/`pz` loads placed after the first subtraction).
- Float scratch values (`x`, `y`, `z`) used by the distance loop were the same function-scope locals as the later spawn
  position: that gave the target's callee-saved float registers.
- A record read twice, first as `table[i].idx` and then through `Slot *slot = &table[i]; slot->idx`, kept the target's
  two address forms (`lui; addu; lhu %lo(sym)` and then `addu base; lhu 2()`).

Failed experiments that would otherwise be repeated:

`found && cond`, `found & !x`, `found & (x < 1U)`, `u8`/`u16` flag types and `|=` in the loop all gave 120 to 625.

What is controlled evidence and what is one function:

Three functions of one source file; the flag and return-type results were measured by changing one thing at a time.
