---
title: A save-menu action: switch arms, one store at the join, and a volatile flag that is loaded again
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---
What the function (or case) looked like and the exact compiler and flags:

An N64 save-menu setup function, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, score 30 with six register rows. It
chose an action from a state halfword (`0x19` and `0x1B` give 3, `0x1A` gives 2, anything else 1), stored it in a u16
global and passed that global to the next call. m2c wrote the choice as a nested `if` tree with a range test
(`0x1B > (s32) state`), and the target reloads the global from memory (`sh $v0, G` then `lui $a0; lhu $a0, G`).

The source change, and the measured result before and after (scores, the judge used):

Three edits together, 30 -> 0 (the app's judge): (1) the identical-arm store (`if (x) { G = v; } else { G = v; }`) collapsed
to one store at the join, (2) the tree written as `switch (state) { case 0x19: case 0x1B: v = 3; break; case 0x1A:
v = 2; break; default: v = 1; break; }`, (3) `extern volatile u16 G;` so the later read loads again instead of
using the register. Also dropped the unused `unsigned long long` constant a search had left. Library families:
`range_assignment_switch` (2) and `scalar_reload_volatile` (3), with `identical_arm_collapse` (1).

Failed experiments that would otherwise be repeated:

Alone or in pairs they score worse: switch alone 550 (split-body form 450), collapse alone 340 or 1250, volatile
alone 70, switch with the duplicated store and volatile 70, collapse + split-body switch + volatile 220. Only the
grouped-label switch with the single store and the volatile is exact, so a greedy chain cannot find it: the beam has
to keep non-improving children. Hand-written variants of the `if` tree (a `u64` constant local, a duplicated `else`)
kept the value in `$a0`: the pseudo was tied to the call argument it was forwarded into, and only the reload
(volatile) moves it back to `$v0`.

What is controlled evidence and what is one function:

One function; the volatile qualifier is a hypothesis from the target's reload, not confirmed in the compiler source.
