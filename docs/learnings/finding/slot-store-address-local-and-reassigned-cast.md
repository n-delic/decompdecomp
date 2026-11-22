---
title: slot store address local and reassigned cast
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-22
---

What the function looked like and the exact compiler and flags: a 51-instruction slot setter (`u8 *object`, a `u8`
action, a halfword table index, a scan loop over 29 halfword slots, an index-guarded byte flag store at
`object[0x1fc + i * 8]`, and a final halfword store), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`. The saved best
stood at 175 and was full of scaffolding (a whole-body `do { } while (0)`, a `u8 frame[16]` the target's frame needs, a
`goto found`, `id = (u8) action;` written twice).

The source change, and the measured result before and after (the standalone judge, scores of the app):
- `single_use_inline` (existing): the inline helper's call with a cast argument at its one use: 175 -> 115.
- `cfg_integer_lifetimes` (existing) on a clean copy: the loop counter and the late copy of the action share one
  local, the late copy written where it is used: 500 -> 65.
- `reassigned_local_inline` (new, decomp-repair #1277): `id = (u8) action; if (... | (id == 25)) return 0;` with `id`
  assigned again after the scan loop becomes the test with `((u8) action)` written in: 65 -> 20. Reading sched.c
  (a hypothesis the measurement agrees with): a local with two sets is not a "birthing" insn (`birthing_insn_p` needs
  `reg_n_sets == 1`), so the first scheduler launches its zero-extension ahead of the arithmetic beside it; written
  inline it is a single-set pseudo and stays in source order.
- `scaled_store_address_local` (new, decomp-repair #1275): `object[0x1fc + (i * 8)] = 1;` becomes
  `rec = object + (i * 8); rec[0x1fc] = 1;`: 20 -> 15. Reading expr.c `both_summands` (hypothesis): a sum inside an
  address is expanded with EXPAND_SUM and "put a multiplication first", so the `addu` has the scaled index first; an
  assignment to a pointer local takes the plain binary-operation path and keeps the order written, base first.
  Written with a cast of the base to `u32`, a shifted index or an offset local, the `addu` order did not change.

Failed experiments that would otherwise be repeated: a separate local for the late copy (instead of sharing the loop
counter's) shifted the whole allocation (260-305); `id` assigned inside the test (`(id = (u8) action) == 25`) gave
the target's registers but the extension launched first (60); the same with a second set made single by other means did
not help; swapping the test's operands, `<= 29` for `< 30`, `-1 != i`, a shifted index, operand order of the final
`or`, and the types of `id` (`u8`, `u32`) changed nothing; `u16`/`s16` for `id` was worse.

What is controlled evidence and what is one function: one function; the families' unit tests are controlled. The
remaining rows after these steps are an allocation difference (the post-loop copy of the action takes `$a1` where the
target has `$a3`).
