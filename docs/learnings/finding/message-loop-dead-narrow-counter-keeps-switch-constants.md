---
title: a u8 counter updated in a message loop's empty arm keeps the switch constants in the loop
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

This is the 83-instruction N64 message thread from experiment/message-loop-switch-constant-hoist-invariants.md:
`for (;;) { osRecvMesg(...); switch (*msg) { case 1: if (mode == 10) { } else if (mode < 11) switch (mode) {...} ... } }`.
It is a scheduler client: the callees are osScAddClient and osScGetCmdQ shapes, and the messages are retrace, done
and pre-NMI. Per-function profile sn-n64-281 `-G0 -mips3 -O2 -mfp64`. At loop time the loop held 55 real insns, and
loop.c moved the outer switch's constants 2 and 1 into `$s2`/`$s0`. The target recomputes both on every pass.

The source change, and the measured result before and after (scores, the judge used):

A `u8` local declared at the top and updated in the empty `if (mode == 10) { }` arm, never read:
`n = (n + 1) & 7;` (59 insns) or `n++; m++;` on two `u8` locals (60). Both are byte-identical under the app's judge
(`_judge_with`) once the function's data relocations are restored in memory from its boundary decision. The live
project still lacks those relocations, so the live score is 4500, all of it `%hi`/`%lo` rows (30 x 150). The app's
Check again runs `boundary_repairs.restore_relocs`, which restores 42 relocations for this function (simulated read
only). `u8 n++` alone makes 58 insns, and the 1 is still moved.

Why: `delete_dead_from_cse` (cse.c) drops only sets of pseudos with no uses left, and a register's own update does not
count as a use, so `s32 n; n++;` is gone before loop. A `u8` update goes through subregs (`t = n + 1; n = (u8) t`),
which makes two pseudos that each use the other's set, so both stay. They count in loop.c's `insn_count`
(`threshold * savings * lifetime >= insn_count`, threshold `1 + n_non_fixed_regs` with a call in the loop, minus 3
per move). Flow then deletes them. decomp-repair `dead_narrow_counter` (0.6.86) offers these children.

Failed experiments that would otherwise be repeated:

- A real inner `switch` over {0, 1, 10} with code in case 10 (self-store, `mode = 10`, a dead `long long`): with
  three nodes, balance_case_nodes splits at the middle (1), not at 10 as the target's `== 10, > 10, 0, 1` tree does,
  so the if/else-if shape is right.
- `D_800B8340 = D_800B8340;` in the arm: 58 insns, but the load and store survive to the final code.
- `{ long long t = ...; }` in the arm: 61 insns and right in the loop, but its CLOBBER takes an 8-byte frame slot.
- `{ u64 t = 0; }`: deleted by cse (a REG destination).
- A two-local `s32` cycle (`b = a + 1; a = b;`): 58.

What is controlled evidence and what is one function:

This is one function. The insn counts come from its loop dumps, and the byte identity from the app's judge with
restored relocations. The pass reasoning was read in the gcc-2.8.1 sources (cse.c, loop.c). It is a hypothesis that
the original had a counter whose debug print was compiled out.
