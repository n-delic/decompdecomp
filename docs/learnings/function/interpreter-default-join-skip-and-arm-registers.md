---
title: interpreter default join skip and arm registers
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 1315-instruction N64 sound-sequence interpreter of interpreter-result-merge-and-narrow-case-locals.md (KMC GCC 2.7.2,
the app's `-G8`, the app's judge), continued from the faithful 1770 (the saved 1735 relied on `arm_copy_chain`
scaffolding in one case; this run started from the source without it). Not matched: 1770 -> 785, every step faithful
and every step a library child (decomp-repair family in brackets).

The source change, and the measured result before and after (scores, the judge used):

1. The release/default layout (blocker 1 of the earlier notes). jump.c (2.7.2) cross-jumps an unconditional jump
   first against the insns before its label (`find_cross_jump (insn, JUMP_LABEL (insn), 1, ...)`), then against
   the label's other jumps (`jump_chain`, latest first, only for labels older than the pass). With `default: r = 0;`
   last, every `r = 0; break;` arm matches the default's `move $a0, $zero` and goes to the old default label; the
   next sweep merges those arms wholesale (23500). The target's default jumped, at jump2, to a label of its own past an
   insn between the two labels: the copy of the result into the tested variable. Source: `default: done = 0; goto
   command_test;` last, `done = commandResult; command_test: if (done & 0xFF) break;` after the switch, the arms'
   result a `u8`, and the release code (the `goto` target after the switch) written into case 6's `else` under
   `case 0x0:` (case 1's else `goto`es it). Only all of it together: 1770 -> 1400; the default skip alone 1830 (7305
   with an `s32` result), the release move alone 6010 [`switch_default_join_skip`, decomp-repair #1617].
2. Case 0xB's register rotation (blocker 3): `t = (f - d) << 16; t = (t * s) >> 7; t += (w >> 24) << 16;` in the one
   user variable instead of one sum expression; the product chain keeps that variable's register (1400 -> 1335)
   [`sum_accumulate_steps`].
3. Case 0xC's `if (p) { ...; r = 0; break; } r = 0; break;` without the arm's own tail: it falls into the shared one,
   which jump2 cross-jumps into case 0xB's tail (`j` with the last store in the delay slot), 1335 -> 1085
   [`arm_tail_share`].
4. The slot-check helper's `$v0`/`$v1` swap (blocker 4): the compare written in each arm (`if (T[slot] == slot)
   return 1;` / `if (R[bank][ch] == slot) return 1;`), so the values are block-local; the tails cross-jump into one
   (1085 -> 1055) [`compare_into_arms`].
5. Case 0x37's load order (blocker 7): the global read first into the case's existing `s16` temp (`t = G; lv = f + t;
   t = lv;`), 1055 -> 995 [`operand_preload_temp`] (the plain `lv = G; lv += f;` gave 1010).
6. Case 0x30's tail merging into 0x39's (blocker 6): 0x39 as `if (t <= 0xEA60) { p->f28 = t << 15; } else { p->f28 =
   0x75300000; }`; the constant arm is last before the join, so 0x30's `sll` no longer cross-jumps into 0x39's, and
   the per-arm stores give the value `$v0`. Together 995 -> 785; inverted alone 1260, stores alone 995
   [`inverted_clamp_store_arms`, #1621].

Left at 785: the command fetch's `&D_800FDB9C` in `$v1` (blocker 2), the pan clamp's `andi` (blocker 5), the default's
`move $a0, $zero` (ours sets `done` in `$v0` and jumps: `done` inherits `$v0` from the test's `zero_extend` temp and
the result's call-copy preferences, global.c line ~790; the target's `done` was in `$a0`, so the copy was a no-op), and
the init block's `ori` order. The `volatile u32 frameSpace[12]` frame scaffolding is still in the source.

Failed experiments that would otherwise be repeated:

- The default skip with a `u8 done` (copy `(u8) r`), an `s8`, `u16`, same-type copies, `register`, the release arm
  also setting `done`: `done` always takes `$v0` (7025, 6880, 1700); the switch moved into a `static inline u8`
  function with `default: return 0;`: the same `$v0` and an 8-byte larger frame (7053). A self-copy `x = x;` at the
  join is deleted before jump2 (23385).
- Case 0xB's operand orders, staged `w`/`p` locals and `f`/`d` locals of other widths: all 1400 or worse (the
  multi-set variable of step 2 is what moved it).
- The slot helper's types and compare order with the shared `x`/`y`: invariant.
- The pan clamp's sum type, a stored-then-read value, `(s16)`/`(u16)` tests: 1055 or worse.
- The command word as `u32[4]` (5466), with a goto loop (8118); the goto loop alone 5303.

What is controlled evidence and what is one function:

One function; each step measured in order with the app's judge (fast_judge, each new best submitted), and each
family's child reproduces its step from the state before it. The jump.c rules are read from the source; the
allocation explanations are read from the `-dl -dg` dumps (global.c's preference propagation) and are hypotheses
where not traced insn by insn.
