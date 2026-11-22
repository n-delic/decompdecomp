---
title: a byte-cursor table walk, its shared offset local and a void tail: each alone worse, together 0
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

A 108-instruction N64 function (a two-iteration loop over parallel global tables: the player's record word is compared
against each entry of three tables, and on a hit a message is shown and a flag set, then a fallback call when nothing
hit) sat at 100 from m2c's draft: five rows, all in the preheader. The target sets the counter, then the three
constants the loop pass hoists (`-1`, `1`, `3`), and only then the walking table pointer (`lui`/`addiu $s2`); the draft
put the pointer first.

What decided it (read from the first scheduler's notes, `passes.py --sched-verbose`, and the target): the preheader
block's insns all have priority 1 and no in-block users, so `rank_for_schedule` breaks the ties by original insn order
(sched.c: `INSN_LUID`); a pointer the C sets before the loop therefore always precedes the constants loop.c emits
before the loop note. The pointer has to be emitted *after* them, which only a derived address does (the entry for
two earlier functions, COMPILER_MATCHING_LEARNINGS.md Round 20). The target also keeps the counter (a plain
`sll $s0,$s1,2` for the other tables, not reduced) and tests the flag as `move $v0,$a2` (a returned value), not the
`andi $v0,$a2,0xff` a `(u8)` test gives.

| edit from the saved best (100) | score alone | combined |
| --- | --- | --- |
| first table read as `*(s32 *)(&D_A + counter)`, cursor and its step dropped | 2083 | |
| the shared `temp = counter * 4` spelled at each use | 2287 | 400 with the read |
| `void` -> `s32`, `v = callee(); return v;`, `v == 0` without `(u8)` | 720 | 0 with both |

None helps alone; the indexed read leaves the shared offset (cse merges `counter << 2` with it and the loop pass reduces
the lot into a byte-offset counter), the inlined offset alone loses the cursor's role, and the tail is a separate
`move` row. Together: 0, recorded by `compile_and_diff`. The dead cursor may stay or go (both 0).

Failed on the way (do not repeat): moving the cursor's initialisation (before or after the counter's, into the
function's first block, into a `for` header, inside `do { } while (0)` wrappers: 100 or worse, the order is the same);
`D_A[counter]`, `(counter << 2) + (s32)&D_A`, `(char *)&D_A + counter * 4` for the first read (2083-2393: all reduced
into one byte-offset counter); a derived pointer assigned inside the loop (1618-2083).

One function, not controlled. decomprepair `walked_table_index` (0.5.656) offers the three edits as one child (and each
half alone); from the saved best its first child is the exact one. Scaffolding in the matched source: none beyond the
indexed spelling (the result local returned from a function the draft made `void` is the callee's value, which the
target keeps in `$v0`).
