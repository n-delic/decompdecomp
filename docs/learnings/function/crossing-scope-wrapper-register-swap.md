---
title: A scan loop's head scope, jumped into by a goto, outweighed the loop limit: two saved registers exchanged
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

The source change, and the measured result before and after (scores, the judge used):

Failed experiments that would otherwise be repeated:

What is controlled evidence and what is one function:

twine `func_8007AEA4`, a 67-instruction N64 pick-from-a-table search (4-byte records, a retry loop of three attempts, a `goto` scan loop), KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, the project profile. Search families: `crossing_scope_unwrap` (decomp-repair), found by hand.

The saved best was at 20 with the target's instructions all present and only `threshold` and `limit` exchanged between `$s5` and `$s4`. `passes.py` showed the saved-register ranking: threshold 5 weighted references over 21 live insns (0.476) ahead of limit 4 over 18 (0.444), the reverse of the target. The five references of threshold came from the use `entry[4] < threshold` sitting inside a `do { entry = entries; scan: ...; id = ...; } while (0);` that the loop tail jumps into (`goto scan`) and an arm leaves (`goto advance`). Inline-helper vs direct compare, a use added to limit, and wrapping other statements all left 20 or went to 70.

Edit list (alone / combined): take that one wrapper off, keeping the braces: 20 -> 0, one edit, the app's judge (`compile_and_diff`) recorded the match. Adding a use of the limit instead (a second `if (limit == 0)`) or wrapping its test or set in a one-shot scope pushed it above the actor and exchanged `$s2` and `$s4`: 70.

The existing unwrap families (`weight_scope_unwrap`, `one_shot_unwrap`, `operation_scope_unwrap`) refuse a wrapper with a label or a goto crossing it, so none of the 60 children `prove_chain.py --families all` scored reached it (best 39). `crossing_scope_unwrap` (decomp-repair, PR 1207) takes such a wrapper off when it has no break, continue, switch or declaration; on the saved best it emits 45, 0 and 80.

The weight explanation (a wrapper counts as a loop for flow.c's reference weights) is a hypothesis from the allocation table, not confirmed in the compiler source. One function; the unwrap family is tested on a synthetic shape.

Scaffolding the match still carries from the saved best (not added here): `volatile s32` locals for the stack slots at 0x14 and the spare words, and a `(entry || entries) != 0.0f` condition that duplicates its arms. Polishing owns those.
