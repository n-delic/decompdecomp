---
title: a loaded counter stored plus a constant and tested afterwards needs an old-value copy and an in-place increment
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function looked like: a 16-instruction N64 function that counts calls in a field of its argument and installs a
second callback once the old count passes a bound. m2c writes `t = M2C_FIELD(p, s32 *, 0x8C); M2C_FIELD(p, s32 *, 0x8C) =
t + 1; if (t >= 0xB) { ... }`. The target loads into `$v0`, copies it (`move $v1,$v0`), increments `$v0`, stores `$v0` and
tests the copy (`slti $v1,$v1,0xb`). The single-expression source allocates the sum in `$v1` and tests `$v0`: score 120
(one missing `move`, four register rows).

The source change, and the measured result: `u = t; t = t + 1; M2C_FIELD(p, s32 *, 0x8C) = t; if (u >= 0xB)` scored 0
(the app's judge). `t += 1` instead of `t = t + 1` also scored 0. Library family `increment_old_value_copy`
(decomp-repair: `increment_old_value_copy`, `_early`, `increment_in_place_store`) emits it: from the single-expression
source 120 -> 0, from the m2c draft 420 -> 120 (`unknown_symbol_offsets`) -> 0 through `prove_chain.py`.

Failed experiments: the copy alone (`u = t; store = t + 1; if (u >= 0xB)`) stays at 120; so does the in-place increment
with the test left on `t` (it then reads the sum). Both edits are needed together. Before the family the chain from the
same sources only reached 15 through scaffolds (`entry_parameter_clobber`, `entry_scheduling_scope`, `entry_base_init`: a
zeroed parameter copy inside a `do { } while (0)`), which is not a faithful program.

What is controlled evidence and what is one function: one function, measured; the register-role explanation (the copy is
the quantity live across the compare, the in-place variable is the one set again) is a hypothesis from the allocator's
behaviour, not read in the compiler's source. False-positive load of the family over one project's sources: 1 of 1002
matched sources and 14 of 944 open functions' bests produce a child, 1-2 children each (not judged).
