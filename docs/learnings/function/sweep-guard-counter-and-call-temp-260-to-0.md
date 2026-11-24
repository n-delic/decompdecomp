---
title: a 996-instruction collision sweep, 260 -> 0: one flag-mask local, the guarded counter zeroed above its guard as u16, the call's mask temporary reused for the byte load
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The collision sweep of `function/sweep-region-method-first-pass.md` (nested object-list loops, six triangle loops,
each pair guarded by `flags & 4`), KMC GCC 2.7.2 `-O2 -G0 -mips3 -mgp32 -mfp32`. Saved best 260 (score version 3).

The source change, and the measured result before and after (scores, the judge used):

App judge through `fast_judge.py`, each step submitted with `setup.py try`.

| edit | score |
| --- | --- |
| saved best | 260 |
| the hand-off note's seed: the `& 4` values of all three object tests in one `u32` local of their own (the first one had shared the call-mask temporary) | 170 |
| the first loop's `var_s0_2 = 0;` moved above its guard `if (flag < count)` and the counter typed `u16` (every read was already `& 0xFFFF`) | 60 |
| the 0x48 byte read into the call-mask temporary, then masked into the flag-mask local: `temp_v0 = obj->b48; mask = temp_v0 & 4;` | 0 |

Why each step works (read in the 2.7.2 source):

- The guard's delay slot. The target has `beqz $v0, join; move $s0, $zero`; with the zero inside the guarded block,
  reorg's eager fill takes the join's `li $v0, -1` from the branch target instead (one instruction more). With the zero
  above the guard the slot is filled from above. But an `s32` counter zeroed there is in the same cse class as the
  flag the guard compares (both known 0), and `make_regs_eqv` heads the class with the register that lives longest:
  the counter, so the guard compares the counter and combine folds `0 < n` to `n != 0` (`sltu` gone, frame + 8).
  A `u16` counter is an HImode pseudo, never in the SImode flag's class (`finding/u16-local-is-an-himode-pseudo-and-narrows-its-arithmetic.md`).
- The call result's `andi $v0, $v0, 0xffff` scheduled after the next call's argument loads: sched.c's
  `birthing_insn_p` boosts an insn whose destination is set exactly once, and sched1 schedules backwards, so the
  zero-extend into a single-set temporary lands late. A second set (the byte load) removes the boost; the mask itself
  written into the temporary (`temp_v0 = obj->b48 & 4`) made it a global pseudo and it took `$v1` (15).

Library: decomp-repair `guard_counter_init_hoist` (`_u16` child) and `call_temp_load_reuse` (PR 1578); each reproduces its
step from the before-state (170 -> 60, 60 -> 0).

Failed experiments that would otherwise be repeated:

- Dropping `var_s7 = -1;` (a misread of a paged assembly listing): 2751. The target does set it.
- Zero above the guard with the counter `s32` 225, `u32` 225; the flag `u32` 910, `s16` 425; the guard on `keep` 300.
- The then-arm and else-arm loops with separate counters: 280.
- The call result as `& 0xFFFF`, `register s32`, `(u16)` casts at the uses, the two masks swapped: 60-91. One temporary shared
  with the second section's call: 50 (the order right, the register `$v1`).

What is controlled evidence and what is one function:

One function. The rules (reorg's fill order, cse's class head, sched's birthing boost) are read in the compiler source.
