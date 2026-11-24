---
title: void returned local fenced and early exit
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-24
---

What the functions looked like: object constructors of 25 to 54 instructions (allocate, initialise through a
constructor call, a dozen field stores through the object), built by SN GCC 2.8.1 with `-G0 -mips2 -O2` and
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The target ends with `move $v0, $s0` (the object kept in
a saved register, the last stores addressed through `$v0`): the function returns the object. m2c typed it `void`
because no caller reads the value, so eight of them sat at 120 to 230 until the return type was changed to `void *`
and `return temp_v0;` added as the last statement (the matches' only change).

Two shapes the library family `void_returned_local` (decomp-repair 0.5.1027) did not reach, both measured read-only
with the fast judge on the saved bests of the open functions:
- a saved best with `do { } while (0)` fences between its last stores: the return alone scored worse (225 -> 325) and
  the same return on the fences opened (`scaffold_strip.unfence`) scored 0. The fence keeps the scheduler from moving
  the copy to just after the last call, which the target's `move $v0, $s0` needs. The family now also builds its children
  on the unfenced function (`void_returned_local_unfenced`, only for a function that has fences).
- a saved best with a bare `return;` before the object exists (`if (f() == 0x30) { g(a); return; }`): the family
  declined it, and the matched twin keeps that exit as a bare `return;` in the `void *` function (the early path's
  `$v0` is the last call's result, as in the target). 130 -> 0.

Across the project's other open functions (768 with a saved best) the family now offers children for 11 void ones and
every one improves, none to 0 alone: 325 -> 15, 785 -> 380, 1090 -> 775, 670 -> 550, 705 -> 600, 825 -> 705,
1440 -> 1330, 1460 -> 1335, 2395 -> 2295, 335 -> 295, 365 -> 245. The unfenced child was worse for two of the three
fenced ones (335 -> 505, 365 -> 565): the fence there is load-bearing, which is why both children are offered.

Failed experiments: the return alone on a fenced best (325); copying the local into a second local before the last
store (the `move` is not added); `s32` / `(s32) local` typing scores the same as the pointer (the target's bytes can't
tell them apart).

What is controlled and what is one case: eight constructors of one game reproduce the rule; the 11 open functions are
measurements, not matches. The scaffolding the match relies on is the return itself: a function nobody calls reading its
result.
