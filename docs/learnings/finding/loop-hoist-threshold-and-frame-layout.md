---
title: loop.c hoists a lone 0.0f below ~62 real insns; matching a frame by declaration shape
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

A ~1000-instruction collision sweep (nested object-list loops with six inner triangle loops, each calling a helper
and then testing `out >= 0.0f && out < best`), KMC GCC 2.7.2 `-O2 -G0 -mips3 -mgp32 -mfp32`. The target loads the
zero with `mtc1 $zero,$f0` inside every inner loop. Every candidate instead kept the zero in a callee-saved FP
register (`$f22`, `mtc1 $zero,$f22` before the loops), which costs one more saved FP register, a 8-byte larger frame
and shifts every later register choice. Saved bests had worked around it with `(x + 1) >= (0.0f + 1)`, which only
moves the problem to a hoisted 1.0f.

Rule (read from `cc1 -dL`, whose dump starts with "Loop from A to B: N real insns" and one line per movable;
consistent with loop.c `move_movables`, hypothesis not checked against the 2.7.2 source, which is not installed):

- The compare needs a register, so each `>= 0.0f` is `(set (reg) (const_double 0))` then the compare. The load is
  a movable (single set, set and use in one block, life 1, savings 1).
- It is moved only if `threshold * savings * life >= insn_count`. `insn_count` is the number of real insns of that
  loop at the time of the pass (after the first cse and jump, before combine/flow), `threshold` is about 61 here
  (`-dL` printed "moved" for loops of 56..61 insns and "not desirable" for 62 and more).
- Identical constants in sibling loops are grouped by `m->match`; the enclosing loop then sums their savings
  ("life 4, savings 4 ... moved"), so a group moves out of loops of several hundred insns, and cse2 merges them into
  one function-wide register.
- Pure copies do not count (cse deletes them), nor does anything flow deletes later. What counts and survives to the
  second jump pass is duplicated identical branch arms: `if (c) { A } else { A }` (cross-jumping merges them back)
  added 4 to 10 real insns to a loop.

The source change, and the measured result before and after (scores, the judge used):

Not matched. Evidence only: with all six inner loops at 62 or more real insns (duplicated arms) every inner load
read "not desirable"; with the outer loops turned into `goto` loops (no loop notes) nothing was hoisted and the FP
registers became `$f20`/`$f22` as in the target, but integer allocation then changed (loop depth weights), so the
score got worse (8673 -> 16883). With real outer loops the grouped constants were still moved by the middle loops
(395 real insns, savings 4, life 4). Unsolved: what makes the target's constants non-movable at every level.

Frame layout facts found on the same function (judge scores 8235 -> 8673 as other rows moved, but all local
offsets and 0x14c..0x1a0 spill slots became identical to the target):

- Aggregates are 8-aligned; spill slots sit at 8k+4 offsets (0x12c, 0x134, ...), allocated in the declaration order
  of the spilled pseudos (a parameter copy declared late gets a late slot).
- An address-taken scalar is placed late (end of the frame), while an array or struct is placed at its declaration:
  a scalar the target has in the middle of the locals (between two aggregates, at +0x18 of a union) is an `f32 x[2]`.
- Locals the target overlays (a rotation matrix, a 3-short angle array and a vector at +0x10 and +0x18) are one
  union; the frame gap before the next local was a `char pad[0x30]` member.
- A `volatile` local takes its slot at its declaration and shifts every spill slot by 8; removing it was needed.

Failed experiments that would otherwise be repeated:

- `-fno-strength-reduce`, `-fno-expensive-optimizations`, `-fno-cse-follow-jumps` and other flags change nothing about
  the hoist (score or `mtc1` position).
- Float compare spelling (`0.0f <= x`, `!(x < 0.0f)`), storing the sp18 zero vector as ints, copies and casts in the
  loop body: loop real-insn counts do not move.
- Converting only the inner loops to `goto`: the outer loops hoist the grouped constant.
- `prove_chain.py --families all` from the improved frame found only argument_spill_tail / identical_arm_collapse
  steps worth 5 to 270 points.

What is controlled evidence and what is one function:

One function. The `-dL` readings (counts and moved/not desirable) are direct compiler output; the threshold value
and the grouping rule are inferred from them. The frame facts were each checked by the offsets they produced.
