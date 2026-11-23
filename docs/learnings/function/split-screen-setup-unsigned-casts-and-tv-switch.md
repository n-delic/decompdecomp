---
title: A split-screen setup routine matched by an osTvType switch, plain unsigned float casts and table-element arguments (4500 -> 0)
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 556-instruction N64 split-screen setup routine (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`): it makes sure each
track-marker parent object exists (`registerTrackMarkerParent(k, ...)` when the table slot is NULL) and places it with a
seven-float rectangle helper (`obj, cx, cy, w, h, x, y`), then a loop over the save slots and a switch on the visible
slot count. Saved best 4500 (116 differing rows) after the work in
docs/learnings/finding/float-constant-role-twins.md, which also explains the four role-twin locals the match keeps.

The source change, and the measured result before and after (scores, the judge used; isolated app judge, confirmed by
`compile_and_diff`):

1. The loop's walked cursor (`p = (char *) &slot4 - 0x10; ... *p ...; p += 1;`) indexed by the counter (`p[i]` /
   `gTrackMarkerParents[i]`), and the flag-byte address local (`t4 = (char *) &flags - 1`, set in a block before the
   loop) written where the loop reads it. Each alone 4500 -> 4830; together 4335. The preheader's
   `move $s2,$s1; addiu $s4,$s2,-1` matched. decomp-repair `cursor_deref_index`.
2. `x = 0; if (osTvType) { y = 0; x = y; } else { y = 0; }` written as
   `x = 0; y = 0; switch (osTvType) { case 0: break; case 1: case 2: x = y; break; }`: 4335 -> 4065, prologue exact.
   jump.c's store-flag rewrite of `x = a; if (c) x = b;` looks back for x's value with `reg_set_last` (rtlanal.c),
   which stops at a CODE_LABEL: in every `if` spelling it fires and cse folds the branch away (or the arms stay apart
   with a `j`), while the switch's case labels stop the scan, so the branch and the `move $s3,$s7` copy stay and reorg
   puts the shared `y = 0` in the delay slot. decomp-repair `tv_type_switch`.
3. Every `(f32)(f64)(s32)y` and `(f32)(f64)y` of the unsigned `y` written `(f32)y` (and `(f32)x` likewise), with the
   search's identical-arm guard around the first `registerTrackMarkerParent` dropped: 4065 -> 2545 (casts alone 4235).
   The unsigned conversion is a pseudo set twice (`cvt.d.w`, then the conditional `+ 4294967296.0`), which cse never
   reuses, and cse folds its sign test away where it knows `y` is 0 (the first two calls, which is why the target has no
   `bgez` there); the signed cast was one expression cse carried from call to call (`mov.d` into a saved float register),
   which shifted every float register after it. The guard had cut the cse path that knew `y`. decomp-repair
   `unsigned_float_arg_unify`. (Confirmed in the dumps: `.cse` had two fewer `float:DF` than `.rtl` with the signed cast.)
4. The helper's object arguments as table elements, `gTrackMarkerParents[4]`, `[5]`, `[0]`, instead of the alias
   symbols at those addresses (`gMenuRootObject` is element 4): 2545 -> 1005. The loads moved after the x conversion and
   the loop's base became `lui $t0,%hi(element 4); addiu $t0; addiu $s1,$t0,-0x10`. No family: the library does not know
   which symbols are elements of which table (the app does).
5. The loop as `for (i = 0; i < gSaveTotalSlots; i++)` instead of `if ((s32) y < gSaveTotalSlots) { i = 0; do ... }`:
   1005 -> 0. The target's guard compares `$s7` (y) only because cse substitutes the oldest register known to hold 0 for
   the counter's 0 in the copied entry test. The library's `loop_weight_scope` also reaches 0 from 1005.

Still relied on: the identity inline (`return p + 0;`) for the flag byte's base register and the four role-twin
locals (one per argument position, see the finding above). The `while (x) x--;` and `if (y) x = 1;` label tricks that
also kept the prologue copy (4295, 4325) were dead code and were not kept.

Failed experiments that would otherwise be repeated:

- Spellings that try to stop the conversion reuse with the signed cast: a shared `f64 d` (`d = x; ... d = (s32)y`,
  4115-6180), `f32 fx, fy` temporaries, inline or out-of-line conversion helpers (4930; an out-of-line call proved
  the reuse was the only cause), `s32`/`s16`/`u16`/`s8` types for y, identical-arm guards on the other null checks
  (5190: they also rematerialise the 240/320 constants, which the target shares).
- Six extra foldable branches before the second call to exhaust cse's path length (PATHLENGTH): the reuse stays (cse2
  reruns it).
- A pointer local or array for the flag bytes in place of the identity inline: 4910-5861.
- Prologue spellings with `if`: both zeroes before it, each arm, `else` copies, a `do { } while (0)` or `goto` label
  between: 4335-5635.

What is controlled evidence and what is one function:

One function. The jump.c/rtlanal.c rule in step 2 was read in the 2.7.2 source; the cse explanation in step 3 is from
this function's dumps (`.rtl` vs `.cse` conversion counts), not a controlled toy.
