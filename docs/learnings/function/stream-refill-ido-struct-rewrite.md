---
title: stream refill ido struct rewrite
kind: function
compiler: ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 222-instruction stream refill in an N64 game's audio code (a ring buffer, a DMA read through a callback, two loops
that advance a cursor by 0xA0 and call a decode routine and a clear routine). The project's compiler is KMC GCC 2.7.2,
under which the saved best (m2c with casts, 14498) was hopeless; the target's own idioms say IDO 7.1: `or $s0,$a0,$zero`
moves, branch-likely delay slots (`bnel`, `bltzl`), `divu` followed by `bnez; break 7`, and a dead `sw` after a `b`.
`try_compiler` under `ido-7.1 -O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul -woff 649,838,712` (the range the
project already had for the neighbouring functions) reached 335 register-only on the old C; a function-only `suggest`
with a source that matched under it saved the profile for this function.

The source change, and the measured result before and after (scores, the judge used):

Typed-struct rewrite of the whole function (the exact judge under the IDO profile, one score per step; every step is
an equivalent program, none is scaffolding):

* struct with the 0x190..0x11DE fields instead of cast-and-offset reads: 655
* `len = n * 4; if (n > 0x100) { len = 0x400; }` as `if (n > 0x100) { len = 0x400; } else { len = n * 4; }`: 555
  (the branch layout of the target: `bnez; sll` and a `b` over the constant). The condition has to be written with the
  clamp as the then arm: `if (n < 0x101) ... else ...` scored 1115.
* the cursor update `s1 = p->head + 0xA0; s2 = ...; v1 = s1 - p->tail; p->head = s1;` as `p->head = p->head + 0xA0;
  s1 = p->head; s2 = ...; v1 = s1 - p->tail;` (the store first, the local read back): 300
* the three locals that cache `p->head`, `p->size` and `p->head - p->tail` replaced by the reads where they are used,
  in both loops and the tail: 60 (singles 1070, 300 and 240; only the combination pays)
* the second loop's guard `if (A && B && C && D) { do { .. } while (C && D); }` as `if (A && B) { while (C && D) { .. } }`:
  0. Its last two rows were a `move $s1,$t4` before the store and the `addiu $s3,-1`/`beqz` pair; no statement order or
  spelling of the body (a `t` local, `--budget`, the decrement before the update, `s1 = p->head = ...`) moved them
  (120 / 60), the loop's form did.

The four edits are decomp-repair families, chained from the typed-struct source they reach 0 (655 -> 555 -> 300 -> 60
-> 0): `default_expression_into_else`, `staged_store_first`, `reload_temp_inline`, `guard_prefix_while`.

Failed experiments that would otherwise be repeated:

* the else arm written `if (n <= 0x100)` or as `n < 0x101 ? n * 4 : 0x400`: both 1115, a different branch layout.
* loading `size` before `p->head - p->tail` (declaration and assignment order of the three locals): 395 / 190 with
  other rows moved; the plain reads load left to right as the target does.
* `s1 = t = ...` and `p->head = s1 = ...` spellings of the second loop's update: 2553 / 2808.
* The registers of the whole function were right from the first compile under IDO; the 14498 under KMC was the compiler.

What is controlled evidence and what is one function:

One function. The scores are exact-judge measurements under the IDO profile; why the plain reads beat the cached locals
is not read from IDO's source (a hypothesis: it keeps a value live across a loop only when the source does).
The families' checks (no store, call or jump between a definition and its uses, equal definitions through a loop's back
edge, field and local of one width) are unit-tested on small sources and were run on this function only.
