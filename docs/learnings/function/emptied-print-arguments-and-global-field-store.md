---
title: a call to an emptied callee keeps its arguments, and a global record's last byte store sits with the first two
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 64-instruction N64 object routine, drafted by m2c at 660. It calls a callee whose own matched C is empty
(`jr $ra; nop`, an emptied debug print), then copies three bytes into a global record and clears four words.

The source changes, scored by the app's judge:

- The draft's `callee();` under `void callee(void);` became `callee(arg0, D_message);`: the target loads the object into
  `$a0` and a string's address into `$a1` before the `jal`. Alone 660 -> 330. Without them the symbol is never loaded.
- The third byte store (`M2C_FIELD(&D_record, u8 *, 2) = ...`), which m2c wrote after the four word clears, moved back
  beside the other two. Alone 450; with the call's arguments, 0. A struct or an array declared for the record scores
  the same, so m2c's spelling can stay.

Families: `stripped_call_arguments` (new) writes the arguments, and `statement_slide` / `statement_slide_far` now read
`M2C_FIELD(&G, ...)` as a cell. From the draft the two reach 660 -> 330 -> 0.

One function; the argument evidence is read from the target, the statement order is a judged hypothesis.
