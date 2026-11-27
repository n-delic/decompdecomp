---
title: o1 live length and fence forms
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

Seven N64 functions of 373 to 832 instructions (drawing code, menu state machines), written as typed C from the
assembly under gcc 2.7.2 KMC, `-G0 -mips3 -mgp32 -mfp32 -O1`. All were recorded as -O2 and scored in the thousands
there. Three matched with little or no adjustment; for the others the last rows were allocation or cse.

The source change, and the measured result before and after (scores, the judge used):

The app's judge throughout.

- Two saved registers swapped between a parameter and a hoisted constant (250): copying four byte loads through a
  `u8` local before the wider local adds live instructions with no change to the code (250 -> 15). Hypothesis, from
  the `passes.py` table: the extra live length lowers the parameter's global-alloc priority below the constant's.
- A table index overwriting its source register (15): `(n = i * 14) + ...` in a call argument, with a local another
  block uses (15 -> 0). Library: `o1_register_forms` (`subterm_into_idle_local`).
- A table address kept in a saved register across a test and a later call (1967 in the starting source): an empty
  `do { } while (0);` before the call between them (library `empty_cse_fence`, 1967 -> 0 with that one child).
- A constant that has to reach the delay slot after a read through a volatile pointer, with the two registers swapped
  (50): one pointer local shared by two switch cases for two different globals (50 -> 0). Its added references let the
  pointer outrank the flag. The delay-slot filler would not move an instruction across the volatile access.
- A `u8` returned local and the parameter in each other's saved register: declaring the local `s32` (150 -> 1 row).
- `x--; if (x == 0)` rereads the field; `if (--x == 0)` tests the value just stored. `if (a < b && flag)` became a
  branchless `and`; the target had two branches, so nested `if`s.

Failed experiments that would otherwise be repeated:

Casts, `while` for `for`, `!= 0`, copies of word-sized values and float temporary chains changed no live length: the
compiler removes them before flow. Only narrow (`u8`, `s16`) copies of loads added any.

What is controlled evidence and what is one function:

The two `o1_register_forms` children were each judged alone on their starting source. Everything else is one function.
