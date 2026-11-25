---
title: one-shot scope weights for a 2 percent priority gap
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-25
---

What the function looked like: a 1713-instruction N64 bit-stream decoder (16 inlined field reads through NIBBLE/ONEBIT/
BITS macros over a byte cursor and a bit position). The saved best (1025 fp32, 1225 recorded fp64) had the target's
instructions everywhere; 201 register rows were left: the cursor `p` in `$t0` where the target has `$t1`, a decoded
field `sz` (and the clamped width sharing its register) in `$t1`. The -dl/-dg table: p 416 refs / 1813 live
(1.836), sz 74 / 247 (1.798).

The source change and the measured result (dev judge, fp32), each a one-shot scope that emits nothing:
- `BITS(sz, n2);` -> `do { BITS(sz, n2); } while (0);`: sz 76 refs (1.846) beats p: 1025 -> 20 from the saved best,
  1285 -> 280 from the natural store order.
- The saved best's `cur.v[1]` store moved before `v[0]` (an earlier library step) back to v[0], v[1], and
  `do { o->cur[j].v[1] = y; } while (0);`: the field `y` (6 * 76 / 1351) now beats the loop's row-offset giv: 0.
- Then the exact -mfp32 trial was applied with suggest: matched.
decomp-repair `macro_statement_scope` and `store_value_scope` (module statement_weight_scope): a greedy chain over
their children reaches 0 from the natural-order source.

Failed experiments: every order of the `sx = sy = sz = 0` chain (identical code, 1025); scopes around both sz stores
(295: the row offset then outranks y); around the zero chains (reorders the copies across the fence: 60-120); around
sz's sign extension (2159); every BITS/NIBBLE/ONEBIT in a scope (1930-2415: p gains as well).

Controlled evidence vs one function: one function; the priorities were read from the cc1n64 -dl/-dg dumps for every
variant, so the mechanism (flow.c's loop-depth weight on references, global.c's order) is checked, not guessed.
