---
title: SN 2.8.1 nested loops tested at the head, and a record pointer taken at the block head
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-28
---

A 209-instruction triangle emitter (five nested counted loops, a screen-box reject test, leaf) whose project profile is
KMC GCC 2.7.2 sits far closer under this SN profile (saved best 7065; plain C 10037; end of this run 835, not matched).

The source changes, scored with the app's judge-worker under the SN profile, each on the source before it:

- Every loop keeps its test at the head and a `j` back. `for` and `while` are rolled; so is `for (;;) { if (c) break; }`.
  An exit by `goto` to the statement after the loop, or `while (1) { if (c) { body; } else break; }`, is not.
  A `for` with `continue` needs the `continue` written as a `goto` to a label ahead of the step. All five loops at once:
  plain `for` rewrite 6960 -> 3640 (decomp-repair `loop_else_break_all`).
- Three table lookups with every slot address built before the first load: `p0 = &table[i0]; ... a = &verts[*p0];`
  1920 -> 1320 (`index_slot_addresses_first`).
- `element = &out[count];` as the first statement after the loop's exit test, the stores through `element->`:
  1320 -> 850 (`element_pointer_block_head`).
- Integer-first address sums (`i * 4 + (u32) table`) gave the target's `addu` operand order: 1315 -> 1285.
- `(s16) x > clip->max` rather than `clip->max < (s16) x` put the halfword's shift before the bound's load: 1285 -> 1165.

What is left: the model-pointer guard (the target has `beqz $a0` with `move $v0,$zero` in the slot; early return, result
variable, `goto fail` and parameter reuse all kept the model in `$v0` or filled the slot with the argument copy), the
first loop's header copy (`move $v0,$v1; addiu $v1,$v0,8; move $t3,$v1`) and registers in the second loop.

Failed or no better: a separate table variable for the second loop, taking the pointer after the second index read,
reusing the index variables for the loaded vertex numbers (4630), SN 2.7.2 builds (1475, 4885).

All of it is one function; the compiler reasons (`expand_end_loop` rolling) are read from GCC's source for 2.7.2 and
are a hypothesis for SN's builds, which have no source.
