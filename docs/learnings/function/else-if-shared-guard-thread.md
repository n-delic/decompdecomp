---
title: else if shared guard thread
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

An 84-instruction N64 predicate, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`: an `else if` ladder whose second arm
repeats the first arm's first test (a byte against a local copy of a global, then against the global).

The target sends the failing first test past both arms to the third. The saved source's ladder sends it to the second arm
(no jump threading at -O1), one branch target (10 points). Writing the test once around both arms, `goto` past the rest,
fixes it. The rest of the chain: a `u8` result (`narrow_masked_local`), a `do { } while (0)` around the last arm's test
(`run_weight_scope`), then this step (decomp-repair `else_if_guard_thread`): 1095 -> 315 -> 10 -> 0, app judge.

Failed: bitwise `|` instead of `||` in the arms left the score unchanged; -O2 was far worse (2665).

Controlled: one function. `else_if_guard_thread` turns the 10 into 0.
