---
title: A global read at the head of a run of record stores (SN GCC 2.8.1, entity initialiser)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 46-instruction N64 entity initialiser: three halfword stores of one constant through the parameter, byte stores of
constants, an id taken from a global counter (`G + 1`, not stored back), one call that allocates a second record, and
the same fields filled in it. Saved best: an m2c draft with raw `M2C_FIELD` accesses, 730.

The source change, app judge (`fast_judge`, then `compile_and_diff`: exact):

1. `serial = G + 1;` moved from beside the arithmetic to before the first store of the run (730 -> 330 alone;
   460 -> 60 after step 2). decomp-repair `global_read_head`; `statement_slide` stops four places short.
2. The field copied from the first record written first in the new record's block (730 -> 460 alone).
   `field_copy_to_head`.
3. The raw stores written as struct members (60 -> 0). `m2c_field_record_view`. With raw casts the same order
   scored 60 to 330: a store through a casted pointer may change the scalar global, a member store may not
   (hypothesis for the pass; the scores are measured).

The same match by hand, typed: halfword stores first, `serial = G;`, then the type byte store, `serial++` later, and
the copied field read into a local at the head of the `if` block.

Failed experiments that would otherwise be repeated (typed source):

- A local for the repeated constant, assigned first: 270. Wrapping that assignment in `do { } while (0);` gave 60,
  with the `$ra` save above the `$s1` save: the loop notes stop the prologue's stores moving. The fence was not the
  game's source.
- Setting the constant local twice to stop sched1 treating it as a single-set register (`x = 0x28; x <<= 8;`,
  `x = 0x2000; x |= 0x800;`, a second assignment after the call): all folded back, 270.

One function; the chain was measured with only the three families above.
