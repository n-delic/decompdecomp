---
title: A defaulted call result stored in each arm, and field copies read again
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 63-instruction N64 object update: two read-modify-writes, a `u16` position stored and compared, and a call result
defaulted to `0x3000` when zero, stored, read back and divided by 44.

The source change, and the measured result before and after (the app's judge, standalone and live):

- Typed rewrite from the assembly with `t = f(p); if (t == 0) { t = 0x3000; } p->limit = t;`: 415. The same with
  `if (t == 0) { p->limit = 0x3000; } else { p->limit = t; }`: 0. The target has the default in `$v0` in a
  `beql` delay slot, one `sw` and a read back of the field.
- From the saved best (m2c draft with `do { } while (0)` fences, 765): the same edit gives 490; then every local that
  only copied a field written as the field, a stray `a1` argument dropped and the fences opened: 0.
- decomp-repair: `conditional_store_tested_arms`, then `field_copy_reread_arity`, chain from the saved best to 0.

Failed experiments that would otherwise be repeated:

- `p->limit = t ? t : 0x3000;` 300, `p->limit = f(p) ?: 0x3000;` 605, a static inline "nonzero or default" helper and
  `if ((t = f(p)) == 0)` 415.
- From the saved best the copies removed one or two at a time were each worse (375 -> 510, 665, 140); the fences left
  in place kept the copy-free child at 120.

What is controlled evidence and what is one function: all one function. Why the read back survives in the two-arm
form (cse running before the arms' stores are merged) is a hypothesis.
