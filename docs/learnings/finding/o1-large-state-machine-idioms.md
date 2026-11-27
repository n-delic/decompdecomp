---
title: Last steps on large -O1 state machines and spawners (shared case bodies, equality-first default, loop slot arrays, status-check macros)
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

Ten functions of 456 to 1464 instructions in one N64 game went from saved -O2 drafts to exact as typed C written from
the assembly, with real switches and every call's full argument list, judged by the app's judge under the profile above.
Each row is one function unless it says otherwise.

- **Three states sharing one body, one saved register too high on the object pointer** (270 -> 0 with the constant
  below fixed): write the body once per `case`. Cross-jumping merges them after allocation, but the references count.
  decomp-repair `case_label_copies`.
- **`x = 0; if (n != 1) x = f() % n;` where the target extends `n` again after the call** (180 -> 0):
  `if (n == 1) x = 0; else x = f() % n;`. The label before the call stops cse sharing the divisor with the test.
  decomp-repair `default_into_then` (it now reads a `{` on the `if`'s line).
- **A counter set to zero before two calls, then a `for` over it**: with its own variable, combine moved the `= 0` down
  to the loop's entry test. Sharing the variable with another arm's small local (which lives across a held address)
  put it in the target's register and the set where the target has it (1055 -> 270).
- **A global array of halfwords read in a loop without the address hoisted**: declare the elements as a one-member
  struct (`Id ids[8]; ids[i].v`). A count the target holds an address for is a struct member at a non-zero offset.
- **`Vtx d[p][k]` rows picked by a runtime index**: `d[p][idx * 4 + k]` on `Vtx d[][16]`, not `d[p][idx][k]`, which
  cse relates to the previous pointer (`addiu 0x10`).
- **Car slots through a base 0x5F4 bytes in**: `cars[i].slots[j]` on the car array, not a second array at the field's
  address, so loop.c holds `base + i * stride` outside the inner loop (205 -> 0 with the double below).
- **One `else` that stores the same value as an inner arm, with the inner copy the survivor**: the outer `else` is a
  `goto` into the inner arm (1020 -> 0). This is a label added to reproduce cross-jumping's choice.
- **`t = (f32)(rand % K)` converted into the variable's own register before a float load, with the other operand's
  address computed before the call**: `t = obj[i].x + (t = rand() % K);` (100 -> 0). The nested assignment is a
  spelling found to reproduce both, not a likely original.
- **A status check repeated after two calls where the target reloads its constants each time**: a
  `do { r = f(); if (r == 2) goto a; if (r == 3) goto b; } while (0)` macro; without the loop notes cse holds 2 and 3
  in saved registers across the second call (2250 and 905 -> 0, the second with plain `if`s).
- **A switch tree with a `slti` before the last equality test**: the source had a case that goes to the default's code
  and whose test was then deleted. Its value cannot be read back; any value between the neighbours reproduces the
  tree (`case 0x64: case 0xC8: break;`).
- **A score of 1 with no differing rows** was a `double` constant three times: read it with `read_data` before writing
  the literal (the values were 2.05 and 0.2, not the round guesses).
