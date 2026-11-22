---
title: arm argument copy join sched1 order
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

**Function and compiler.** A 150-instruction N64 effect spawner, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, saved
best 60: every instruction right, one reorder in the tail after a four-path switch. The target has `lw $a1; lhu $a2;
andi $v0,$s0,0xfff; move $a3,$s6; jal; sh $v0,0x3e($s2)`, ours had `move $a3` before the `andi`. Judged with the app's
judge-worker, then confirmed with `compile_and_diff` (byte-identical).

**What the first scheduler does (sched.c read, the `-dS` trace confirms).** sched1 schedules the block backwards. After
the call, the store is picked first (the greatest potential hazard of the equal-priority group), which blocks the two
loads for a cycle or two; the insns that fill that window are picked by priority, then by original position (later
first). The masked value (`andi`) is a birthing insn (`birthing_insn_p`: single-set pseudo, live afterwards), so it
becomes ready with the launch priority and is picked first; `move $a3` is a hard-register set (never birthing) and comes
second, which puts it before the `andi` in the output. Making the `andi` non-birthing (the masked value in a `u16`
local) only moves the problem: the `move $a0,P` (original position after the `andi`) now wins the tie and fills the
window, and the `andi` falls to the top of the block (520).

**The edits (judge-worker scores on the same function).** (1) saved best 60. (2) `u16 m = E & 0xFFF; *(s16 *)(..) = m;`
alone 520. (3) the call's first argument copied into a local set at the end of each of the four paths: 115 (the block's
rows all right; two saved registers swapped). (4) the copy on two paths only, written as one arm setting it and a
`default: join:` label the other arms `goto` (the last one falls in): with (2), 0; without (2), 60. Which arm sets the copy
itself does not matter (all three choices 0). One set (every arm going to the label): 520; three sets: 115.

**Why the swap at three or more sets (hypothesis from the allocation table, `passes.py`).** Each set is another reference to
the pointer stored through in every arm; `floor_log2 (refs) * refs / live` then ranks it (12 refs, 70 live: 0.514)
above the record pointer (12 refs, 79 live: 0.456) that the target allocates first. Two sets give 10 refs (0.428).

**Why the copy helps (hypothesis).** The copy is a multi-set pseudo cse cannot fold into the pointer, so it is allocated to
`$a0` at each set: the `move $a0` lands at the ends of the paths (the delay slots of their jumps take it) and the join
block, which is scheduled as one block, no longer has a filler with a later position than the `andi`.

**Failed experiments.** Statement-order grids of the tail (`word`, `half`, store, argument locals; 24 and 336 orders),
`do { } while (0)` around every pair of its statements, the store written into the call's argument list at each
position, a `const` struct parameter (removes the load/store dependences: still 60), the store as a struct member,
per-function `-mcpu` and scheduling flags (only `-mcpu=r4000` and `-mcpu=r8000` compile and they equal the default),
`arg = arg_in; arg_in = 0;` parameter overwrites (65), every `prove_chain.py` family from the best (no child improved).

**Evidence level.** One function, with the compiler's sched.c, the ready-list traces and the allocation table as the
mechanism. Library family: `arm_argument_copy_join` (decomprepair 0.5.654), wired into the draft repair path and the
Deep Permuter's local lane.
