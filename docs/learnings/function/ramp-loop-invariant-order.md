---
title: ramp loop invariant order
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:
A 168-instruction N64 menu function (KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): a `do` loop over a table of records, each computing an alpha (`0xFF`, or `0xFF - (-diff * 22)` clamped to 0..0xFF) and storing `(*flags & ~0xFF) | alpha`. Saved best 340 (m2c's text).

The source change, and the measured result before and after (scores, the judge used):
Edits on the saved best, exact judge (fast_judge): drop m2c's `& 0xFFFFu` on the product and write the product inline instead of a copy local (340 -> 340, the `move` row goes); `u16` alpha local (every use already said `& 0xFFFF`: 460, the 255 is now hoisted by loop.c instead of reusing the alpha register, a different mode from the SImode constant, hypothesis); `&sym` written where the loop reads it, as `*(s32 *)((char *)&sym)`, not a pointer local before the loop (180: the hoisted invariants take the target's registers); the `+= 6` of a record field split around the next global load, load / load / add / store (60); a named constant `k = 0xFF` set before the loop-entry copy `loopItems = items` (0), which orders the preheader li before the copy. Global.c ranks the three hoisted pseudos by live length, so the one born first gets the highest register; that is the reading, a hypothesis. Each edit alone is neutral or worse; prove_chain's greedy best was 225 (a union scaffold). decomp-repair `ramp_loop_shapes` (PR 1285) offers each edit and the cumulative chain; the full chain reaches 0 from the saved best.

Failed experiments that would otherwise be repeated:
`menu = sActiveMenu` before the `+= 6` without the `do { } while (0)` around the 0x96 constant moved the constant's register (500); the fence first (590); the named constant after the loop-entry copy left two reordered rows (60); `*(&sym)` instead of the byte cast lost the hoisted `lui` (725).

What is controlled evidence and what is one function:
One function; the 340 -> 0 chain is measured, the pass explanations are not confirmed in the compiler source.
