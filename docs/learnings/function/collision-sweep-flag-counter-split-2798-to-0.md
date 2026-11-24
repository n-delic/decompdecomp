---
title: collision sweep flag counter split 2798 to 0
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 1928-instruction N64 collision sweep of `collision-sweep-call-runs-and-frame-scaffolding.md` and
`collision-sweep-cursor-fold-frame-and-guard-label.md` (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`), handed over at
2798 with the object pass's two segment addresses (`&sp108`, `&sp118`, target `$s6`/`$s5`), two spills (hit index at
0x22c, `&sp168` at 0x224) and the frame 0x10 short, and the actor pass's counter and cursor swapped.

The source change, and the measured result before and after (scores, the judge used):

Each step scored with the app's judge standalone (`fast_judge.py`), each best submitted; the app records 0.

| step | score | family |
| --- | --- | --- |
| the ray walk's counter: m2c had reused the flag `temp_s1` (`if (temp_s1 < n) { temp_s1 = 0; do .. temp_s1 += 1 ..`) whose guard reads the flag; the walk written with the next walk's counter `var_s4_3`, and `&sp108`/`&sp118` set once before the object loop (the per-walk copies merged) | 2798 -> 1484 | `flag_counter_split_hoist` (decomp-repair #1625) |
| `hitIndex` declared at the top between `sp224` and `sp234` (spill slots follow pseudo order) | 1484 -> 1467 | `declaration_scope_hoist`-like, by hand |
| `s32 sentinel_neg0 = -1;` (a search's `sentinel_constant_local`) back to the literal in `hitIndex != (-1)` | 1467 -> 487 | `sentinel_local_inline` (#1625) |
| the three walks' mask tests as `x & sp1F6` / `x & sp1FE` (the spilled mask's reload register first in the `and`) | 487 -> 457 | grid |
| the ray walk's `var_s4_3 = 0;` above its guard (reorg fills the guard's delay slot from above the branch) | 457 -> 357 | `guard_counter_init_hoist`; `flag_counter_split`'s moved child does both (1384 from the saved best) |
| the halfword XZ walk's cursor as `p = base; p += i * 6;` like its byte sibling | 357 -> 347 | by hand |
| the object loop's head: `sp1EC, sp204, sp108_arg, sp20C, sp214, sp118_arg` (grid over the six) | 347 -> 125 | grid |
| the actor walks' counter zero wrapped `do { do { i = 0; } while (0); } while (0);` (both walks) | 125 -> 55 -> 0 | scaffolding, by hand |

Why the split works (from the `-dl -dg` dumps): with one pseudo for flag and counter (35 refs over 302 insns) the
flag kept `$s4` across both ray walks, so `&sp108` (50 refs, 1004 insns, 0.249) found all nine saved registers taken and
was spilled while the hit index (0.211) and `&sp168` (0.163) got registers in non-conflicting walks. Split, the flag
dies at the guard, the counter shares the byte walk's `$s4`, both addresses get `$s6`/`$s5` and the hit index and
`&sp168` spill, as in the target; the frame becomes 0x290.

The actor walks: the cursor giv (24 refs, 47 insns, 2.043) outranked the counter (24 refs, 50 insns, 1.920) and took
`$s0`. The counter's zero sits before the guard (its delay slot), so its life cannot shrink without losing the slot (a
zero inside the guard: counter right but the slot takes the join's `li $v0, -1`, 220). Two one-shot scopes around the
zero add two loop-weighted references (26 refs, 2.08): faithful code, but scaffolding. global.c breaks equal priorities
by allocno number, so a tie also goes to the counter (it is the older pseudo).

Failed experiments that would otherwise be repeated:

- The split alone 2773; the hoisted addresses alone 5551 (see the hand-off notes for the others).
- Folding the actor cursors into `tri[1]`, `tri[2]` (`cursor_lockstep_fold`): identical code (the cursor is a giv
  either way).
- `u16` actor counters with plain `i < n` tests: identical code.
- One scope around the increment instead of the zero: 270 (the scope moves the bound's load in the scheduler).
- One scope around the zero: 110 / 55 (one walk); two are needed.

What is controlled evidence and what is one function:

One function; the priorities are read from cc1's `.lreg`/`.greg` dumps of the candidates. The match relies on
scaffolding: the two doubled `do { } while (0)` around the actor walks' counter zeros. The two families are hypotheses
tested here. Not yet a family: the statement-order grid at the object loop's head, the mask operand order, the scope
weight on a counter's zero, and the `hitIndex` declaration placed by spill-slot order.
