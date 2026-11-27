---
title: a store as a comma operand of a call argument, and a sum named inside a difference
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 98-instruction N64 actor setup, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Its m2c saved best
scored 450 with 9 differing positions: `addiu $v0, -0x200` where the target has `addiu $s0, 0x200`, and the order of a
`jal`, its delay slot, two argument loads and the `li` of a byte stored just before a two-argument call.

The source change, and the measured result before and after (scores, the judge used):
Judged by the app's standalone judge, in order from the saved best:
1. `(a + 0x200) - (f() >> 5)` is folded to `a - ((f() >> 5) - 0x200)`; `(t = a + 0x200) - (f() >> 5)` is not:
   450 to 240. Family `named_sum_difference`.
2. `do { v = g(p); p->x6A = v; p->x38 = v & 0xFFFF; } while (0);` round the call and its two stores: 240 to 60.
   `run_weight_scope` already offered this, but declined the whole source because of the `#define` block above an m2c
   function; it now declines only directives inside the function.
3. `p->x78 = 5; r = h(p->x0, p->x2);` as `r = h(p->x0, (p->x78 = 5, p->x2));`: 60 to 0 (the app records the match).
   Loading both arguments into locals before the store gave the same bytes. Family `argument_comma_store`.

Failed experiments that would otherwise be repeated:
- Sixteen prototypes and local spellings of the halfword call result (u16 or s16 return, `& 0xFFFF`, casts, a chained
  assignment) all scored the same.
- Moving the byte store after the call (380) or into a one-shot scope of its own (220) does not give the order.
- A separate `t = a + 0x200;` statement before the line is not the same as the assignment inside the expression.

What is controlled evidence and what is one function:
One function. That the folder's reassociation and the scheduler decide steps 1 and 3 is a hypothesis; only the byte
results are measured. The match relies on the one-shot scope of step 2 and the assignment of step 1.
