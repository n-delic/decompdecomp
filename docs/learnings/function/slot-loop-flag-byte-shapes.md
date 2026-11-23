---
title: A slot-update loop's flag byte, word local, scratch name, halfword parameter, comparison temps and constant flag (375 -> 0)
kind: function
compiler: gcc-2.7.2-kmc, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

A 214-instruction N64 slot-update loop (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): for each 8-byte entry it tests a
mask against a button word, updates five flag bits of a two-byte record with read-modify-write steps and a bit-edge
detector. The saved best was 375 (m2c shape plus earlier agents' scaffolding: identity inline, `volatile` casts, a
three-arm `one` flag with a never-taken `if (!one) goto`). Final: exact (isolated judge 0, then the app's judge).

The measured steps (isolated judge; each row is the source change and the score after it, in order):

| step | change | score |
| --- | --- | --- |
| 0 | saved best | 375 |
| 1 | `s1 = 0;` in a `do { } while (0)` (`clamp_default_scope`) | 330 |
| 2 | `int a2` -> `u16 a2` (it is only read as `(u16) a2` and holds a byte) | 320 |
| 3 | `v = p[idx].a; if (s1) v |= 8; ...; p[(ull) idx].a = v;` -> `p[idx].a \|= s1 ? 8 : 0;` | 240 |
| 4 | the last short use of `s1` (`s1 = (v >> 2) & 1; t = s1 ^ ...`) given its own local | 230 |
| 5 | the same for `v` and `t1`; `crossing_scope_unwrap` removes a doubled `do` | 140 |
| 6 | `e = inline_fn2((u16) i, sizeof(*arg0), (u32) arg0)` -> `ii = i; e = (SlotEnt *) ((ii * sizeof(*arg0)) + (u32) arg0);` (`u16 ii`), fence removed | 80 |
| 7 | `t2 = t2 == 0; t3 = a2 == 0; if (t3 & t2)` -> `if ((t2 == 0) & (a2 == 0))` | 70 |
| 8 | the `if (s1) one = 1; else if (!s1) one = 1; else one = 0;` chain moved from before `if (!one) goto advance;` to just after `s1 = 0;` at the loop top | 0 |

Compiler facts behind them (read in the pinned source and the cc1 RTL dumps; hypotheses where marked):

- Step 3: the temporary and the separate store give the store its own address computation after the guard's join; the compound update expands the
  address once, before the load, and the store reuses it. Step 6: `cse.c` `make_regs_eqv` makes a user pseudo the canonical register of its
  quantity only when it lives beyond the cse block, otherwise the older temporary stays canonical and combine cannot fold the return-value copy
  (`addu $v0; move $s0,$v0`); a halfword *parameter* of the inlined helper is a `reg/v:HI` of its own, which is why the later `p[(u16) i]`
  addresses are recomputed after the join (the target's shape) and not merged with the first (650 when the helper is expanded with its argument
  substituted). A `do { } while (0)` between the pointer and its first load stops that copy propagation (loop notes end the cse path) but also
  makes the load a scheduling barrier ahead of the global read it precedes in the target (`sched.c`, loop notes), so the halfword local replaced it.
- Step 8 (hypothesis): the flag set at the top is live across the loop body's call; the function uses all nine call-saved registers, so global.c leaves it
  unallocated, reload spills it, and `reload_cse_regs` rebuilds the constant before the branch in `$t0` (the first register nothing else used) with no
  stack traffic. Written just before the test it took `$v0` and the store before it could not move into the branch's delay slot.

Failed experiments that would otherwise be repeated: `&arg0[(u16) i]` or any direct pointer expression for `e` (655: the later entry reads merge with
it); the volatile spelling of the global read or of the entry's first byte (580-665); `do { } while (0)` fences around the first byte's load or
the flag reset in every combination (all 230, the load ahead of the global read stays); `t1 = D_800CEEB0;` before the load (hoisted out of the loop);
the flag as `u8`/`u16`/`s8`, other spellings of the redundant chain, an inline helper that returns 1 (260-330, none gave `$t0`); `-fno-*` flags on the
function (all worse or equal); a compound `|=` with the flag set inside the arms (565).

Controlled and one-function evidence: the chain from the saved best was run through the library (`prove_chain.py --families all`): byte_flag_chain,
narrow_bool_shapes, crossing_scope_unwrap reach 70 from 375 and constant_flag_hoist then reaches 0 (decomp-repair 0.5.708 and later). Every step is one
function; none is a controlled regression. The match relies on scaffolding it inherited: the identity inline `inline_fn23`, the `(volatile SlotEnt *)`
read of the flags byte, the `(volatile SlotOut *)` read of the record byte, `inline_fn`/`inline_fn234` byte readers and the never-taken `one` branch
(its constant chain is what a real source compiled to; the source it came from is unknown).
