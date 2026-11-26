---
title: a constant a loop stores stays in the loop when its local is set twice there
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 300-instruction N64 billboard-strip builder (default `sn-n64-281` profile). Its vertex loop writes three vertices
per point, the third colour byte `v->cn[3] = 0;`, `= 0x80;`, `= 0;`. The target keeps `addiu $v0,$zero,0x80` inside
the loop, right before the middle vertex's colour stores, and hoists only the 0xFF and the three table addresses. Our
literal store hoisted the 0x80 into `$t4` for the whole loop, and every register of the loop shifted (2535, about 60
register rows). An earlier session measured the threshold (docs/learnings/finding/loop-constant-hoist-threshold-measured.md):
no spelling of the literal store gets it under `move_movables`' threshold in a 70-insn loop.

The source change, and the measured result before and after (scores, the judge used):

Read in loop.c (gcc 2.8.1 source): `count_loop_regs_set` marks a register `may_not_move` when it is set again in the
loop after a use of its previous value, and `scan_loop` never makes a movable of such a register. A literal is a set
of a fresh pseudo (a movable); a local assigned twice in the loop is not:

```c
        alpha = 0x80;            /* was v->cn[3] = 0x80; */
        v->cn[3] = alpha;
        ...
        alpha = 0;               /* was v->cn[3] = 0; */
        v->cn[3] = alpha;
```

fast_judge (the app's judge): 2535 -> 1930 with a new local (the `0x80` row now matches, register and place); writing
all three stores through it: 1930; through a dead local of the function's own (the vertex count `nv`, assigned
only after the loop): 1350, recorded in the app. decomp-repair `store_constant_web` (0.6.56) offers the new-local
children; `dead_scratch_reuse` then renames the local (1375 on its best child).

The same mechanism is the likely cause in a 516-instruction particle billboard renderer of the same game: its target
sets `$t2 = 0xFF` once per vertex (four sets, the register otherwise free between them), while our merged 0xFF is the
first movable loop.c moves; none of the spellings tried there (chained stores, a colour local per vertex, `c = 0xFF`
then `c = a`) reproduced the four sets yet.

Failed experiments that would otherwise be repeated:

- A local set to the constant once per vertex and never to anything else (`c = 0xFF;` before each vertex's stores):
  identical code. cse deletes a set of a value the register already holds, so it is set once and moves.
- The alpha local set to 0 at the top of the body as well: 1930 (no gain over the plain pair).
- Each vertex in a block of its own with its own pointer: 1955.

What is controlled evidence and what is one function:

The loop.c rule is read in the 2.8.1 source; the scores are one function (same seed, one edit changed each time).
