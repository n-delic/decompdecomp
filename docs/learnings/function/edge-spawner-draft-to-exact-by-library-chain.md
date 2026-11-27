---
title: m2c draft of an object spawner to exact: retry loop, u16 index, guarded range switch
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 187-instruction N64 routine that puts an object on a random open screen edge (three dispatches on one `u16` edge
index, a retry loop over four flags, two clamps). The saved best was the m2c draft, score 3780, after about 40
attempts. A matched look-alike in the same game was only 80% similar, below the twin draft's 0.9 floor; its C
transplanted unchanged scored 7442, so it was a guide for the hand source, not a draft.

By hand, plain source written from the target and the look-alike was exact on the first compile.

Reduced from the draft, step by step (the app's standalone judge):

- flag reads as `u8` (`flag_table_byte_read`): 3780 -> 3380
- the guard, counter from 1 and `goto` out as `while (test) { if (++n == 4) {...} step; }` (`guarded_retry_loop`):
  3380 -> 2250. `goto_backward_loop` and `loop_else_break` keep the guard and score nothing like it.
- the `u32` index read as `& 0xFFFF` declared `u16`, its word copies written as it (`masked_read_local_narrow`).
  With the copies kept the dispatch computes the extension early.
- the dispatch tree as a `switch` (`embedded_if_tree_switch`)
- `if (v >= 0) { if (v < 2) A else if (v < 4) B }` as grouped-case switches (`guarded_range_switch`): on a `u16` the
  tree is `sltiu` with the sign test folded away; the switch is the target's `bltz; slti`
- each clamp's result local folded into the value both arms add to (`arm_result_into_addend`) and the clamp's test
  written the other way (`branch_orientation`)
- `i = (i + 1) & 3;` as `i++; i %= 4;` (`counter_wrap_remainder`): 10 -> 0. `i += 1; i &= 3;` stayed at 10,
  `i = (i + 1) % 4;` scored 1095.

The children of those families applied in that order to the draft reach 0.

Failed: initialising the four bounds to 0 at the top (640 -> 1610); swapping their declaration order (no change).

Controlled: each score above on this one function. Not tried: a greedy search of the whole chain. `prove_chain` from
the draft takes `branch_polarity` first (3300) and the switch steps are worse alone, so a best-child search may not
reach it.
