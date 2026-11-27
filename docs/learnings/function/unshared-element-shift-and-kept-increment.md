---
title: unshared element shift and kept increment
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 48-instruction N64 spawn routine: it updates two counters of an object, then copies one entry of a table of
two-halfword records into a new object and steps the table index. Project profile for the function:
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. All scores are the app's judge.

Two source facts decided it:

- **The increment before a zero store.** The target has `lw v0,0x40; lw v1,0x48; addiu v0,v0,1; sw v0,0x40;
  bnez v1; sw zero,0x40`. `o->n++; o->n = 0; if (o->w == 0)` loses the increment. `o->n++; t = o->w; o->n = 0;
  if (t == 0)` keeps it. Rewrite 1935 -> 1125, machine draft 1709 -> 1219. decomp-repair `guard_load_before_overwrite`.
- **Two shifts of one index.** The target shifts the index twice (`sll v1,a0,2` and later `sll a0,a0,2; ori a0,a0,2`).
  `T[i].x` and `T[i].y` share one shift and read `2(base)`. Writing the second member as `j = i * 2 + 1;
  ((u16 *) T)[j]` gives the target (1125 -> 0). So does the inline integer form `((i * 2 + 1) * 2)` added to the base
  (140 -> 0 from m2c's `(i * 4) | 2`). `((u16 *) T)[i * 2 + 1]` without the local stays at 385: the front end
  distributes the constant. decomp-repair `flat_member_subscript`.

Failed experiments: the first member as a `u16` subscript `i * 2` (shares the shift the other way, 385 or 400);
`(i << 2) | 2`, `(u32)` casts, a local holding `i` shifted in place, and `do { } while (0)` around either load
(140 to 750).

Also seen, one function only: from the machine draft, after the first fix, dropping a cached table-base local gave
1219 -> 500, dropping the draft's `do { } while (0)` fences 500 -> 235, and moving the first load after the pointer
store plus a `u16` read 235 -> 80. The last rows were the order of two loads. The exact source came from a rewrite,
not from that chain. The matched source keeps `x`, `py` and `y` temporaries that turned out unnecessary.
