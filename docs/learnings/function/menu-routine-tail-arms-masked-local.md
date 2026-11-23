---
title: a 578-instruction menu routine: tails written in each arm, a masked local as u8 and two scheduling levers took 865 to 0
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: verified
date: 2026-11-23
---

The function: a 578-instruction N64 menu-setup routine, an m2c draft that an earlier search left with scaffolding (do-while
wrappers, an identical-arm guard, shared scratch names). Saved best 865 (69 register rows), isolated judge
(`fast_judge`, the app's own), then the app recorded 0 (matched). `prove_chain --families all` from the saved best found no
child that improved on it (1164 children); every step below is one function, so the evidence is verified for this function
and an observation for the rules. Steps in order, with the score after each:

| edit | score |
| --- | --- |
| remove the identical-arm `if (markers || savedByte)` guard, unwrap two weight wrappers, wrap one statement of the pointer the target puts in `$s6` (the saved registers are then `$s4`..`$fp` in the target's order) | 865 -> 1070 (worse: the temporaries now differ) |
| the two `inline + (savedByte - 0x111)` sums spelled with the offset assigned inside the expression (`(off = savedByte - 0x111)`), and the two `idx * 4 + (s32) markers` sums with the index first | 1070 -> 1040 |
| the masked local `temp_a0_2 = flags & 0xF` declared `u8` | 1040 -> 660 |
| the byte of the first arm set split into its own local (`ba`), one statement wrapped for one more weighted reference of the record pointer | 660 -> 610 |
| the tail after the three-arm chain (a store of `(value & ~0xFF) | byte`) written in every leaf with the leaf's own value/byte, the arms' trailing byte read moved into the nested leaves (`store_tail_into_arms`) | 610 -> 545 |
| the same for the second chain, whose tail was an identical-arm `if (g) { S } else { S }` | 545 -> 505 |
| the state pointer captured in a local before the byte store | 505 -> 465 |
| the two calls after a float store in their real order (`actorLinkToSurface` then `func_8002B8AC`; the draft had them the other way round) | 465 -> 445 |
| the late `&gPickupSavedByte` local split in two (the web after the actor is created gets its own) | 445 -> 380 |
| the actor argument read as `(s32) *actorSlot` (a volatile read keeps the argument order) | 380 -> 370 |
| the pointer `gProbeObjectList` loaded inside the float store's right-hand side (`probe = G, -3.0f`) | 370 -> 60 |
| the sum's multiplied byte (`inline_fn(savedByte)`, a volatile read) computed into a local before the offset local | 60 -> 0 |

What each one changes in the compiler:

* The tail after an `if` chain whose arms each set the value and the byte: the shared names are pseudos that span several
  blocks, so `global.c` allocates them by priority after `local-alloc.c` has given the tail's own temporaries (the mask `li`,
  the `and`) the first registers; the `and`'s result ties to the mask (the operand that dies and is local), the value
  goes to `$a0` and the mask to `$v0` where the game has them the other way round, and every later register of the block moves
  with them (24 rows). With the tail in each arm all of them are block-local pseudos, `local-alloc.c` ties the `and` result to
  the value, and `jump_optimize` merges the identical tails after allocation. The family is `store_tail_into_arms`
  (decomp-repair 0.5.726); nothing offered it (`carried_store_arms` takes only `store = var`).
* A local that only ever holds `x & 0xF` as `u8` (the local is a promoted subreg): its pseudo no longer inherits the
  register of the `andi` result by preference, and the state pointer and the record pointer fall into the target's registers
  (`a2` and `a1`; 380 points). An `lw $v0,table($v0)` with the destination equal to the index register is expanded by the
  assembler through `$at` (target); with a different destination the temporary is the destination (`lui $v1`): the register
  of the loaded value, not a different source form.
* `global.c` `expand_preferences` merges the preferences of two pseudos when one dies in the insn that sets the other and
  they do not conflict, which is why a copy of a pointer and its source end in one register; a local copy that the store
  and the call argument both use (the target's `move $a0,$v0`, store through `$a0`) could not be made in this function by any
  spelling that kept the copy (identity inline, user copy, `register`): that was the frame loop of another function
  (docs/learnings/function/frame-loop-flag-narrow-giv.md), not this one.
* The first scheduler (`sched.c`) is backward: equal-priority insns are ordered by luid, so which of two independent insns
  comes first is decided by which statement came first in the source. The float store before the load of a global pointer
  (370) and the volatile read after the hard-register argument move (60) were both a luid tie: put the pointer load inside
  the store's right-hand side (after the constant, before the store) and the multiplied byte in an earlier statement.

Failed experiments: a block-local `s32`/`u32` for the masked value (660, as the plain local), typing the table array
`volatile` (1355), `register` on any of the locals (no change at all, a no-op here), the mask as a named local (no change),
`& 0xFFFFFF00U`/`-0x100`/shifts for the mask (no change / worse), `do { } while (0)` around the store/call group in the
tail (370 whatever the grouping), every permutation of the five statements before the formatting call (no change).

Controlled and not: the 865 -> 0 chain is one function. `store_tail_into_arms` reproduces the 610 -> 545 -> 505 steps from the
previous source (unit tests on a small source, and scored on this function's own sources).
