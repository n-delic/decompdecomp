---
title: untyped symbol strides in an actor's pickup update draft
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

A 229-instruction N64 actor update (SN GCC 2.8.1, flags above): a colour cycle, then a jump table over a pickup type
that writes per-player tables. The m2c draft scored 4965 in the app's judge; the saved best, 4050, had a search's
wrong constants.

The draft's causes, in the order the decomp-repair families remove them:

- Per-player tables declared `extern M2C_UNK X;` and indexed by hand (`*((p * 4) + &X)`, `*(p + &Y)`): C scales the
  word again (`sll 4` where the target has `sll 2`) and stores byte tables with `sw`. Each symbol is an array whose
  element is its stride, all at once (`stride_typed_symbol_arrays`): 420. The same child writes out two byte tables a
  pointer picked between before one shared store, and spells `M2C_FIELD((&C[i]), u8 *, 1)` as `C[i + 1]`.
- `x = type - 1; switch (x)` is `switch (type)` with raised labels (`switch_bias_unfold`): 110.
- `part->angle = part->angle + K` through a local copy of `self->part` is `self->part->angle += K`; the target reads
  the member again and copies it (`move $v1,$a1`) (`member_update_reread`): 0.

A typed rewrite by hand (struct types, `glow->red = self->red = table[i]`, plain `if`/`else`) matched too, so the
chained colour stores and the goto structure of the draft were not needed.

Controlled evidence: the chain from the machine draft and from the m2cfix-refined draft, each ending at 0. One
function. Leaving out the switch step stood at 310 and leaving out the member step at 110 (measured on the
intermediate forms). `prove_chain.py --families all` from the first form of the stride child (2515), capped at 40
judged variants, only reached 2160; it was not run from the draft.
