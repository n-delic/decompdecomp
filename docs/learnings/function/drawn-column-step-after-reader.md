---
title: A draft that steps a counter before the call reading it, where the target passes the old value
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 78-instruction N64 function that draws a column of eight rows (the app's judge, `fast_judge.py`). The m2c draft kept
across 704 automatic reuse and repair trials scored 3665.

The four edits, each measured from that draft:

- Three byte globals at consecutive addresses copied one by one into three `s8` locals, only the first passed by
  address: one three-byte record assigned whole (3665 -> 1355). The target takes the first address once and does
  `lb` 0/1/2, then `sb` 0/1/2.
- The draft wrote `y += 0xA; i += 1; draw(.., y, ..)`. The target copies `y` to the argument register on each path to
  the join (`addu $a2, $s1, $zero`) and only then steps it, so the call takes the old value: the draft's argument was
  wrong, not only its order.
- With the steps after the call, `y` written off the counter (`0x3C + i * 10`) and the `goto` arms rejoined as
  `else if` (the last two give nothing alone: 775 with either one, 0 with both once the prototype below is in).
- The callee's fourth parameter declared `f32`, so `0x3F800000` is `1.0f` (775 -> 0 in the chain's order; the
  library already had this one).

Library: `global_byte_run_struct` (new), `counter_step_after_reader` (new; its `_companion` child also writes the
companion off the counter and rejoins the arms), `float_argument_prototype`. `prove_chain.py` reaches 0 from the saved
draft in three steps with them: 3665 -> 1355 -> 120 -> 0.

A clean rewrite (typed record, indexed `for`, `if`/`else if`) scored 180 at once; `i = 0;` before the record copy
(120) and the float prototype (0) finished it. One function; the struct copy and the pre-step argument are the parts
likely to recur.
