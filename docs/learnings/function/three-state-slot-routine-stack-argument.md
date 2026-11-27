---
title: three state slot routine stack argument
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 146-instruction routine with a three-case switch on a slot's phase (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`;
the function's `-O2` scored 5870 on the same source). The saved best was 4795.

The source change, measured with the app's judge:

- Plain C under `-O1`: the record array, a `u16` parameter, `switch`, and prototypes with `f32` parameters for the two
  callees (one takes a fifth argument on the stack). Exact on the first compile with the prototypes (2954 without).
- From the m2c draft (10120 under `-O1`): `indexed_symbol_records` 6048, `stack_argument_value` 4505 (m2c drops the
  fifth argument into an `sp10` local), `embedded_if_tree_switch` on the `return;` leaves 770,
  `reload_temp_inline` 400, `halfword_sign_test` 0 (`t & 0x8000` is `andi`/`beqz`; `(s16) t < 0` is the target's
  `sll`/`bgez`).

What is controlled evidence and what is one function: one function; the three rewrites have unit tests in
decomp-repair.
