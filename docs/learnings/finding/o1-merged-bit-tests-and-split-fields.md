---
title: O1 menus and display lists, merged bit tests, x++ reloads and fenced base pointers
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

Large N64 functions typed from the assembly, each judged by the app's own judge at the profile above.

The source change, and the measured result before and after (scores, the judge used):

- fold-const merges `(f & 0xE) || (f & 1)` into one `f & 0xF` test, and `(f & 2) || (f & 1)` into `f & 3`. A target with
  two `andi` and two branches came from two arms with the same body (jump2 merges them) or from
  `(f & 0xE) ? 1 : (f & 1)`. A 1019-instruction menu state machine went 1580 to 1130 on the first and to 0 with the rest.
- A byte read once directly (`lui; lb` at its own symbol) where the same field elsewhere is reached through the struct
  base: that one access was written with the byte's own symbol (1130 to 75). A stored `int` staged through a spare local
  (`n = i; byte = n;`) lengthened a hoisted constant's range by one insn and swapped two saved registers back, with one
  `do { } while (0)` around a state store (75 to 0).
- `x += 1.0f` on a float field forwards the sum into the next read; `x++` reloads it (400 to 0, 1145 instructions).
- Two field reads off one `la` register: assign the record's address to a pointer and put it in `do { } while (0)`
  (or fence after it); a local for the first-loaded field fixed their order (650 to 30 to 0, 933 instructions).
- A copy loop whose target adds the row offset before the element index: take the row in a pointer local (`line =
  row[c]; line[i]`), one function, 960 to 755.
- Two float registers exchanged with priorities 0.3689 against 0.3691: one `do { } while (0)` around the lower one's
  assignment decided it (320 to 250, register rows only; not a match).

Failed experiments that would otherwise be repeated:

- Two coordinate reads of one 4-byte table entry as call arguments that the target gives two address registers: struct,
  array, pointer, accessor and inline-helper spellings all gave one register.

What is controlled evidence and what is one function:

Each item is one function. `address_then_fence` and `float_step_postfix` (decomp-repair `o1_register_forms`) offer the
last two measured forms.
