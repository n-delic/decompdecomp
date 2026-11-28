---
title: keyframe lerp result in the reused local, one operator per statement
kind: function
compiler: SN64 GCC sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 170-instruction N64 keyframe routine: two counters step through `{time, value}` pair tables, and a fixed-point
interpolation gives a scale (stored three times as `s16`), a height (stored as a word) and from the scale a colour
byte. Target signs: a value address formed as `sll 3; ori 4; addu base` beside time addresses formed as
`sll 3; addu base`; `subu $a1`, `mflo $a1`, `sra $a1,$a1,12`, `addu $a1,$a1,$a0` for the first interpolation, in the
register the colour byte later uses.

The source change, and the measured result before and after (scores, the judge used):

Saved m2c best 4900 (app judge) to 0. Scores from the app's isolated judge.

- The table as flat words read through `static inline s32 keyData(tbl, key, field) { return tbl[key * 2 + field]; }`:
  the column is added to the shifted index before the base, which gives the `ori 4` form (hypothesis: `combine`).
- The phase (`range` first, then the position shifted by 12) as an inline helper taking `s32 *frame`: 900.
- The interpolation written in both arms of the branch (cross-jumping merges it) and the first result in the local the
  colour byte reuses: 80.
- 80 to 0: the first interpolation written into that local one operator per statement
  (`v = b - a; v *= t; v >>= 12; v += a;`) with the phase `t` a block-local of each of the three sites.
  With the phase one function-level local the chain scored 185; decomprepair `local_web_split` took it to 0.

decomprepair `integer_chain_stage` writes the chain: on the expression-form source its child is the exact one
(80 to 0).

Failed experiments that would otherwise be repeated:

- A `{time, value}` struct array, or a two-column array: the value is read as `lw 4(base)` off the time's address.
- The interpolation as one inline function returning the expression (80), `r *= t; r >>= 12; return r + a` inside it
  (80), and its result through a pointer parameter (1279, the local goes to the stack).
- The whole interpolation as a statement macro with its own `prev`/`range` locals (210).

What is controlled evidence and what is one function:

One function. The unit tests of `integer_chain_stage` check the rewrite, not the compiler's behaviour; that
`local-alloc` ties each temporary of the expression form to a dying operand's register is a hypothesis.
