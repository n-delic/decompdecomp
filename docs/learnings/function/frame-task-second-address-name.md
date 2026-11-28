---
title: one address loaded twice needs a second symbol name
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-28
---

A 386-instruction N64 frame function (build the display list, then fill an OSScTask) under
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`. The saved best (m2c draft) scored 16720; a typed
rewrite from the assembly (GBI-style macros on `Gfx *_g = pkt++`, an OSScTask struct, float literals for the
`guPerspective` planes, 16 word assignments for the matrix copy) scored 2735 on its first compile.

Measured with the app's judge:

- `D_END - D_START` stored as the boot microcode's size, and `D_END` stored again in one arm of a later `if` as the next
  microcode's start: cse keeps `&D_END` in its register, so the subtraction takes another register (630). Writing the
  later uses as `data_<same address>` (both automatic names resolve to the same address) gave the target's fresh
  `lui/addiu` and `subu` into the end's register (40). Then `boot = D_START;` before `size = ...;` was 0.
- An address used twice in a run of task fields (dram stack, output buffer end): `D_F` plain both times is 975; the second
  written `D_E + 0x10000`, or named `data_<F>`, is 0.
- After the branches join, the cache writeback takes `&arg->dynamic` and `(u32) glist - (u32) &arg->dynamic`, not the
  local that holds the same address (305).
- `if (size >= LIMIT) return 0; send(); return 1;` and not `if (size < LIMIT) { send(); return 1; } return 0;` (320).

decomp-repair: `address_second_name`, `member_address_respell` (new), `independent_statement_swap`,
`guard_shape_repairs`.

Failed experiment: `D_START + 0xD0` for the later use makes cse add 0xD0 to the register holding D_START.

Evidence: the numbers are one function, each reverting one change from the matched source. Why cse treats the two names
as different values (distinct `symbol_ref`s) is a hypothesis, not read in cse.c.
