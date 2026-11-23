---
title: m2c's register-named locals shared across blocks make a loop's temporaries global
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---
A 150-instruction packed-vector interpolation (a three-float lerp loop, an unpacking loop over 10/11/11 words, a callback loop), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, saved best 75 with fifteen register rows.

Source changes and measured scores (isolated judge; the first scorer was `fast_judge`, the last confirmed by the app):
- lerp temporaries folded into one expression per store (`(b - a) * t + a`, field read twice in text, once in code): 75 -> 65 (`float_temp_chain_fold`).
- The callback's offset scaled in place (`var_a3 = var_a3 * 4`), its two pointer arguments in the locals `temp_a1`/`temp_a2`, and the unpacking loop's `temp_a3` merged into `var_a3`: 65 -> 20 (`call_arg_register_locals`). Alone: shared name 85, in-place offset 65, carriers without the shared name 95.
- `(w * 2) & 0xFFE` as w's last use written `w = w * 2` first: 20 -> 10 (`last_use_double_in_place`).
- The last field's sum written into `temp_v0` (the header word, read only at the entry) instead of the loop's variable: 10 -> 0 (`result_register_named_local`).

Rule (local-alloc.c and global.c in the 2.7.2 source, read; the mechanism is a hypothesis where marked): local-alloc takes pseudos used in one basic block that die once, lowest free register first by priority, before the global allocator sees the rest. The unpacking loop's temporaries were such pseudos, so they took `$v1`/`$a1`/`$a2` and the packed word, a global (two deaths), was pushed to `$a3`. Pseudos that are mentioned in two blocks (m2c names locals after registers, so `temp_a3` in one loop and `var_a3` in another are one variable in the game's source) are global, allocated by priority after the word, and the target's registers follow. Same for a sum: a multi-block local named after `$v0` gets `$v0` where a block-local result took another register and tied the next value to it. sched.c runs before local-alloc here (the compiler's first scheduler), which is why splitting the word into two pseudos hoisted the second load and made things worse (195).

Failed: sharing the loop-1 pointer cursors with the unpacking loop's locals (130-180, the cursors' priorities moved), declaration order of the locals (identical), statement permutations (125+), do-while weight wrappers on the temporaries (110+), the sum in other locals (10-1895).

One function; the individual edits are measured on it, the allocator rule is from the source and the dumps.
