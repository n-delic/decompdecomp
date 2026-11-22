---
title: a bump allocator's (old + mask) & ~mask, the complement after the sum and the sum's variable shared
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function looked like and the exact compiler and flags: a 110-instruction N64 heap allocator (KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`, the project's default profile), a leaf. Before a heap exists it bumps one of two
pointers by tag: one arm rounds down (`end = old - size; end -= end & mask`), the other rounds up
(`start = (old + mask) & ~mask`) with the `nor` of the complement in the branch's delay slot. Saved best 15: three
register rows in the round-up arm (target `nor $v0` / `addu $v1,$a1,$a2` / `and $v0,$v1,$v0`; ours `nor $v1` /
`addu $v0` / `and $v0,$v0,$v1`), the complement first in the arm inside `do { } while (0);` (`arm_invariant_sink`).

The source change, and the measured result before and after (the app's judge-worker; 0 confirmed by `compile_and_diff`):
1. The complement written after the sum, unfenced, and the `and` as `inverse & temp`: every register right, the `nor`
   no longer in the delay slot (15 -> 165; `temp & inverse` 340).
2. The arm's read of a `volatile u32` struct member written `used = *(u32 *)&table[tag].used;`: the delay slot takes the
   `nor` again (165 -> 5; this alone on the 15 source: 15). Making the arm's scalar global non-volatile did nothing.
3. The sum's variable `temp` declared at function scope and used as the free-list loop's alignment remainder (the
   loop's `extra`, same type, disjoint life), the `and` back to `temp & inverse`: 5 -> 0.
Scaffolding: the shared `temp` (one variable for two unrelated values on disjoint paths, plausible for the original but
not provable) and the plain cast on one member read. The earlier `do { } while (0)` weights in the loop remain.

Why (allocation tables from `-dl -dg`, local-alloc.c `combine_regs` and global.c `find_reg`/`prune_preferences` read;
the delay-slot part is a hypothesis): with the complement first, the mask dies at the sum and no pseudo in `$v1` is
live with it, so global.c gives the mask `$v1`; computed after the sum (sched1 keeps it there), the mask is live when
the sum is born and conflicts with it. local-alloc ties an insn's output to its first operand that dies there, and
only when that operand is local to one basic block with one death: `temp & inverse` with a block-local `temp` puts
the result in the sum's register; with `temp` referenced in a second block it is a global pseudo, the tie falls to
the complement (the second operand) and the printed order stays `and $v0,$v1,$v0`. reorg's `fill_slots_from_thread`
did not take the `nor` from behind the volatile member load and did take it from behind a plain one (measured; the
cause in reorg.c not confirmed). Two consequences worth reusing: the operand order of a commutative op decides the tie,
and which operand is "block-local" can be changed without touching the arm by sharing a variable with another block.

Failed experiments that would otherwise be repeated: the complement after the sum inside a fence, every fence span and
statement order of the arm (55..340, 96 combinations); the result returned through a function-scope variable (1165,
1335: a global result loses `$v0` to the arm's end); `start` reused for the complement (240); the sum's variable shared
with the other arm's temporaries in every partition consistent with the target's registers (60..800: it changes the
other arm's schedule); the sum's variable reused for the difference or for the end (285..470, the old/end exchange of
the earlier hand-off at 35); `end = size + start` to give `size` a `$v1` preference (no change); the sum's variable
merged with the loop's remainder while the complement still came first (200).

What is controlled evidence and what is one function: one function, each step scored with the exact judge; the chain is
reproduced from the saved 15 by decomp-repair `complement_late_plain_reads` (15 -> 5) then `disjoint_local_merge_swap`
(5 -> 0). `disjoint_local_merge` could not reach step 3 before: its cross-block merges renamed into a variable not in
scope and never compiled, and `locals_for` read `inverse & temp` as taking `temp`'s address.
