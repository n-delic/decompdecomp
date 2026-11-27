---
title: (s8 *) field spellings hid statement moves, and a copied alloc result kept its second name
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:
A 101-instruction N64 object constructor (a particle spawned from a parent object), `sn-n64-281 -G0 -mips3 -O2 -mgas
-meb -mcpu=VR4300 -mhard-float -mfp64`. A typed-struct rewrite from the assembly matched on its second compile
(615 -> 0: two field copies moved up). The saved best (an autopilot m2c draft, 695) could not be moved there by the
library: its fields are spelled `*(T *)((s8 *) p + off)`.

The source change, and the measured result before and after (scores, the judge used):
From the saved best, with the app's judge (`prove_chain.py --combine 8`):
- a field copied from the parent moved to just after the first copy of its run (695 -> 490, `field_copy_to_head`);
- another copy moved above two constant stores, so its load is issued early and its store sinks (490 -> 285,
  `statement_slide`);
- the volatile casts dropped and the flag's initialiser sunk into the outer guard together (285 -> 170,
  `volatile_cast_unpin` + `guard_temp_sink`);
- `q = p; p = NULL;` after the allocation guard written with `p` alone (170 -> 0, `guarded_copy_merge`, new).

What the library lacked (decomp-repair PR 2011):
- `statement_slide` read `(char *)` byte pointers only, so every statement of an `(s8 *)` draft was a barrier;
- `field_copy_to_head` offered only the head of the run, which scored worse here (1095);
- nothing removed the second pointer.

Failed experiments that would otherwise be repeated:
- `prove_chain --families all` on the saved best before the change: 695 -> 685 within 80 judged variants.
- Alone, dropping the volatile casts or sinking the initialiser did not improve on the state before it.

What is controlled evidence and what is one function:
All of it is one function. That a load stays where the source reads the field while the store sinks is the
scheduler's usual behaviour, not read in the compiler source here (hypothesis).
