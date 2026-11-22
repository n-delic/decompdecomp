---
title: A shared float tail after a label in the last arm: every arm loads the same four floats, and the allocator's preferences put them in $f12/$f14
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

twine `func_800770E4`, a 509-instruction N64 actor state update (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, the project profile). A chain `if (s == 1) { ..; } else if (s == 2) { loads; goto L; } else { if (s == 3) { loads } else return; L: dx = a - x; dz = b - z; field = K; idx = v >> 12 & 15; }` followed by a join label that calls `approximateAtan2(dx, dz)`.

The saved best was at 40: every instruction the target's, eight rows differing only in which float register held what. The target keeps `x, z, a, b` in `$f4, $f6, $f0, $f2` in both arms; ours had `$f0, $f14, $f12, $f2`.

Why (read in the compiler's source and the allocation dump, hypotheses where marked): `x, z, a, b` are set in two arms and read after the label, so they are global pseudos. `global.c`'s `expand_preferences` merges the preferences of a pseudo that dies in an insn with those of the pseudo the insn sets, and the sub's result is copied to `$f12`/`$f14` for the call, so `a`/`x` came to prefer `$f12` and `z`/`b` `$f14`. The target's four values are the local allocator's: local to their own arm (the copy of the run in each arm), allocated in priority order from `$f0` up, and cross-jumping merges the two identical tails afterwards.

Edit list, each alone or with the others (the judge is the app's `fast_judge`, scores as the app records them):
1. swap the two subtractions to the target's order: 40 -> 50 (worse alone);
2. copy the run into the `goto` arm with a private name per arm (`web_split`, floats included): 9450 (the arms' regs differed: the arm kept a union for its pointer);
3. a pointer local of its own for that arm instead of the phase union's member: 9025;
4. the arms' `do { } while (0)` wrappers off: 60 (cross-jumping merged the tails; the floats' length tie put `x` ahead of `b`);
5. the constant `s3 = 0` sunk from before `if (s == 3)` into that arm (its length one longer made the order of `x` and `b` the target's): 1 (all instructions the same; one jump-table entry differed, the judge's "linked jump-table differences" line);
6. `case 7: goto block_71;` before the first case of the `var - 2` switch: 0, recorded by the app.

Failed or neutral: temporaries for the two differences (cse folds them, 50); the run's two orders without the copy (90 vs 50); the run copied but the arms' wrappers kept (9025); `s3` set outside the arm (9410: cross-jumping no longer merged because the two arms differed by one insn).

Library: `goto_nested_run_copy` (decomp-repair, PR 1218) offers steps 2-5 and the order of step 1 as one child; `switch_gap_goto` (same PR) offers step 6 from the target's jump table. The compiler explanations (preference merging, priority ties) are from reading `global.c` and `local-alloc.c` and the dumps, not from running them under a debugger. One function, not a controlled set; the family is tested on synthetic shapes.
