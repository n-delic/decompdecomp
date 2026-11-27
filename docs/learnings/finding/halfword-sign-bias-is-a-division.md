---
title: halfword sign bias is a division
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -O1 -g
evidence: verified
date: 2026-11-27
---

A 54-instruction function with two three-armed switches, each halving a halfword field in one arm (`arg + field / 2`)
and adding the field unhalved in another. m2c writes the halving as a bias statement and a shift:
`T = (u32) (h << 0x10) >> 0x1F; r = (u16) ((s32) ((s16) h + T) >> 1);`.

Source change: the two bias statements written as `(u16) ((s16) h / 2)`. Score 430 before, 0 after (the app's judge,
`fast_judge.py`; the saved draft's fence braces and `u16` locals were left as they were). The bias spelled out inline
(`(s32) ((s16) h + ((u32) (h << 0x10) >> 0x1F)) >> 1`) is 1130: the compiler's expansion of a signed `/ 2` is not
the same RTL as the shift expression, so the division is the only spelling.

Library: decomprepair `sign_bias_shift_divide` takes the halfword and byte forms (statement and inline, `<< 0x10` /
`(s16)`, `<< 0x18` / `(s8)`). One function, verified; the byte form is tested only by a unit test.

Two more steps from the same session, each exact on its function:
- `signed_division` (`q = t >> n; if (t < 0) q = ...`) used to substitute `t / 2^n` into every later `q`, including the
  next `q = ...` that reuses the temporary (the product `temp = a * b` form on an SN 2.7.2 `-O1` function: 695
  before, exact with the product kept in its own local); it now stops at the reassignment.
- A fifth call argument that is a constant on the stack (`addiu $v0, $zero, 0x46; sw $v0, 0x10($sp)`, one load in a
  branch's delay slot serving both arms) with the callee's prototype one parameter short: appending it and widening the
  prototype took 670 to 150, and the word-pointer step `base - 0xC0` written as `(s32 *) ((s8 *) base - 0xC0)`
  (`pointer_byte_offset`) closed the rest.
