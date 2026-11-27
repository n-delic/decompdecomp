---
title: successive allocation results through one scratch local, and a field store moved in two of three blocks
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function looked like: a 107-instruction constructor that allocates three linked objects, each tested for NULL
and filled, in an N64 game. SN GCC 2.8.1 (sn-n64-281), `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`,
the function's own profile. Saved best 2015.

Symptom: the target moves each call's result into `$a0` (`move $a0, $v0; beqz $a0` with `move $s2, $a0` in the delay
slot), stores through `$a0`, and keeps the saved copy only for what reads it later. A variable per object takes a saved
register for every store.

The source change: `e = alloc(); head = e; if (e != NULL) { e->... }` with one `e` for all three, and `head`/`body`
read only by later blocks (decomp-repair `shared_result_scratch`). Judged on the app's judge:
- the saved best with the callees' argument lists fixed (signatures.py): 2315; with the scratch: 1380; then
  `field_copy_group`: 1025.
- the saved best unchanged: 2015, with the scratch 1815.
- a clean typed rewrite with the scratch by hand: 1930, `field_copy_group` 1080, `identical_statement_comove` 540 (the
  kind store up two lines in two of the three blocks), then 0 (the depth store up two lines in two blocks). The last two
  needed the wider `identical_statement_comove`: its limit of 24 children was spent before it reached them, and moving
  the kind store in all three blocks (760, then 220) leaves the third block wrong.

Failed experiments: a separate local per object (`head`, `body`, `tail`) with the clean rewrite scored 2520.

What is controlled evidence and what is one function: every number is from this one function. That the scratch's
register comes from its dying at each call is a hypothesis, not read from the compiler's source. The match itself relies
on no scaffolding.
