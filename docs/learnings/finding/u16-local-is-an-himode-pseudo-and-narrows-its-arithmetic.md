---
title: a u16 local is an HImode pseudo, and arithmetic assigned to it is done unextended
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3
evidence: observation
date: 2026-11-23
---

`u16 v;` is `(reg:HI n)` here (no promotion for locals; `-dr` shows `(set (reg/v:HI 81) (const_int 0))`):

- `v = wide;` is a plain `move`, and every read as an int is a zero_extend (`andi`). cse cannot merge the two registers,
  so the wide value and its 16-bit copy live side by side (the target's `move $t5, $t0` after a position that is later
  read as `andi $v0, $t5, 0xffff`). `((u16) wide)` casts at each use give one register and an `andi` at each use.
- an int expression assigned to a u16 is narrowed by `convert_to_integer` and computed in HImode; `expand_binop` widens a
  HImode add/sub/mult/shift left without extending the operands (`no_extend`), so `u16 x = a + w * i;` is `mult w, i` on
  the raw counter. A `u16` counter read as a factor of a wider expression is extended first.
- an `s32` counter with `(u16) i < n` in the test is incremented without a mask (`addiu s2, s2, 1`, `andi v1, s2, 0xffff` into
  a temporary for the compare), but its first test is folded to `blez`; the `u16` counter keeps the target's
  `move s7, $zero; slt v0, s7, v0; beqz` first test. Only the `u16` form reproduced it.

Library: decomp-repair `narrow_value_shadows` (`narrow_cast_shadow`, `arm_head_copy`, `counter_cast_test`; PR 1434):
from a hand rewrite at 3175 the shadow gives 2585 and an arm-head copy 2175; typing the position `u16` then reached 1885.
One function.
