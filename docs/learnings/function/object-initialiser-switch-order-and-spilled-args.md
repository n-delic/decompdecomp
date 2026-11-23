---
title: object initialiser switch order and spilled args
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags: a 392-instruction N64 object initialiser
(KMC GCC 2.7.2, `-O2 -G0 -mips3 -mgp32 -mfp32`): four arguments (the second spilled to the stack, the third and fourth
in `$s6`/`$fp`), a sub-allocation sized by a four-way `switch` on the third argument, a vertex-copy loop, a run of
field initialisers after a float call, two stream-slot loops and a table search in a loop whose exit test stays at the
top. Saved best 5366 (6131 isolated), m2c scaffolding all over it.

The source change, and the measured result before and after (scores, the judge used: `fast_judge`, the app's own):
the edits, in order, each with the score after it (several are worse alone and pay off with the next):

- argument priorities: `sp1C` copy of the second argument removed, `temp = arg3 & mask` written into `arg3`, the
  tail table search as a structured loop whose test on `arg3` reads the parameter itself (`if (arg3 < 0) .. else v = arg3`):
  a loop's references weigh double in `flow.c`, which lifts `arg3` above `arg1` so `arg1` is the spilled one
  (6131 -> 6506 -> 6791 -> 6617, the target's `$s6`/`$fp`/`sw $a1,0x1c($sp)`);
- the `else if` chain as a `switch` with `case 0:` in front of `default:` (-> 5877; the plain `default` is worse), and
  the `default` body written FIRST in the switch so it follows the compare chain as the target's layout does
  (1252 -> 502, the largest single step; `case 1/2/3` after it);
- the allocation total and the pointer chain in one variable (`p = 0xB8; p += a; ...; p = alloc(p + b)`, cursor
  `p += 0xB8; store(p); p += a`; -> 5692/5282), the size-8 temporary reusing the record-pointer variable (5232);
- paired stores of one value chained, `a[i].x = b[i].x = v` (5232 -> 4577), the 4-iteration loop written back as a
  `for` and the table row loops natural (`slot[i].id >= 0`, `out[i] = ..`; 2177 -> 1747), one counter per width;
- the call's float literals in locals assigned between the call and the store of its result (2597 -> 2177 and
  1747 -> 1612), the exit test of the table search kept at the top of the loop by a local declared in the loop body
  (4402 -> 3312), the offset copied into its own variable before the loop (`temp_s6 = temp_s2`), the slot call before
  the offset arithmetic (1292).

Failed experiments that would otherwise be repeated: a plain `default:` switch (worse than the if chain); `short
temp_s7` (an extension pair in the sum); `case 2/3` as `n * 3 + (n + 2)` (gcc reassociates it to `(n*3 + 2) + n`; the
target's separate `addiu $v1,$s1,2` comes from `w = n*3; t = n+2; w += t`); `var_s0 = 0` hoisted above the offset
(worse); a masked-flag temporary in four spellings (the target keeps it in `$a1`, no spelling moved it); an unused
constant local to add the frame's missing 16 bytes (the target's frame has two stale 8-byte spill slots; each stale
slot in the m2c version came from a constant or copy pseudo that crosses calls; neither a constant local nor a copy of
the argument reproduced them without moving registers).

What is controlled evidence and what is one function: one function (observation). The library families that came out of
it each carry their own measurement: `else_if_chain_switch` (6131 -> 5551 alone), `running_total_cursor` (6131 ->
5756), `paired_store_chain` (5232 -> 4957), `loop_top_test_scope` (4402 -> 3312), `float_literal_locals` (2597 -> 2042),
`param_in_place_update` (worse alone, the argument-priority lever), and the `narrow_return_compare` crash on `0x5EU`
(decomp-repair 1412). Still no family for: the default-first case order of a switch, the zero-offset operand order of a
commutative add, and the stale frame slots.
