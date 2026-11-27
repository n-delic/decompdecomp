---
title: m2c join ladder over an unset saved register, and a halfword read of a byte table
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 73-instruction N64 object update: it spawns a child object, then picks one of four byte-returning calls by a slot
byte and tests the result once. The default path leaves the result unset. The machine draft stood at 1820.

Source changes, each judged with the exact judge:

- m2c's `if (..) { v = f(); goto block_9; }` ladder, `x = saved_reg_s2 & 0xFF;` before the last `if` and
  `block_9: x = v & 0xFF;` inside it, written as an `else if` chain with one `x = v & 0xFF;` after it. `saved_reg_s2`
  is m2c's name for a read of the unset `$s2`, which holds `var_s2`: 1820 -> 655.
- `(u16) *((i * 2) + table)` over `extern u8 table[]` reads one byte where the target has `lhu`;
  `*(u16 *)((i * 2) + table)`: 655 -> 510.
- The `u8` store of the slot byte moved above the three halfword stores (`statement_slide`): 100.
- The selector local `u8` widened to `s32`, removing one `andi` (`narrow_widening`): 0.

The first two had no family; decomp-repair `m2c_join_ladder` adds them, and the four steps then reach 0 from the draft.

A hand rewrite with a typed struct reached 410 and was exact with `member_store_hoist` alone (the same store order).
The saved register alone (rename only) scored 670; the `u8` return types of the four callees alone scored 1470 and
were not needed.

All of this is one function; the compiler reasons were not checked in the compiler source.
