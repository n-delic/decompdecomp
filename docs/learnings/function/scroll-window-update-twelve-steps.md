---
title: A 277-instruction scroll-window update matched from 3427 in twelve steps, nine of them new decomp-repair families
kind: function
compiler: KMC GCC 2.7.2 (-G0 -mips3 -mgp32 -mfp32 -O2), no per-function profile change
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 277-instruction menu-window update (an N64 game, KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, the project's own
profile): it sums the heights of the first `selected` and the first `count` rows of a record array, keeps a scroll byte
inside the window (three guarded stores, a clamp to 0..255), walks the rows until their sum passes the clamp to lower
the selected row, steps the selected row by one inside `0 .. count - 1`, scales the offset, draws every row through a
function pointer with an on-screen flag, and decays the offset toward zero. The saved best was m2c's draft with earlier
agents' scaffolding (a `__repair_phase_bound` local, `if (selected == 0) do { } while`, a wide `s32` carrier for each
clamp) and scored 3427 in the app's own isolated judge (`fast_judge`, the same worker as the app).

The source change, and the measured result before and after (scores, the judge used):

Twelve steps, in the order taken (isolated judge score after the step; steps marked * are decomp-repair families
written for this match, none of which the app's draft path offered for the state before the step, checked by calling
every library module on that state and comparing with the next one):

1. the loop sum's zero set just before its loop, not at the declaration (3427 -> 3237);
2. * both row-sum loops as counted `for` loops over the member (`for (i = 0; i < w->selected; i++)`), the bound locals
   gone (3237 -> 3152): `guarded_loop_for` (also the loop that starts at a member, 3185 -> 3135). The bound is read once
   for the guard and copied for the loop test; the copy comes from loop.c hoisting the invariant byte load and cse
   folding it into a move of the guard's;
3. * the clamp `t = E; if (t < 0) t = 0; else if (t >= 256) t = 255; dst = t;` as one conditional expression into a
   `u8` local that the store and the later `(u8) t` reads share (3135 -> 2505, the largest single step, and the 8-byte
   frame hole disappeared): `clamp_narrow_store`;
4. * the three-way clamp of the selected row, `dst = E < lo ? lo : E > hi ? hi : E`, no local for the result
   (3265 -> 2415 in the match's order, 2505 -> 2280 alone): `clamp_chain_expression`;
5. `u16 y` -> `s32 y` for the row's screen position that is compared and passed on (2415 -> 2155): the library's
   `narrow_widening` already offers it;
6. * a local updated in place by the three arms of an `if`/`else if`/`else`, stored once, given a result local per
   arm (2155 -> 1380): `arm_result_local`;
7. * the halfword member store `w->offset += delta;` moved up past two statements that read byte members, so cse
   reloads those after the store (1380 -> 985): `member_store_hoist`;
8. `if (total - w->scroll < (edge = w->limit))` instead of the assignment before it (985 -> 905): `assignment_at_use`
   already offers it;
9. * `m = selected; if (i <= m) m = i; w->selected = m;` through a second byte local (905 -> 465): `byte_update_copy`;
10. * the walk `while (i <= selected)` with the byte reloaded at its bottom became `while (i <= w->selected)` with the
    arms reading the member, and the arms' sign test written the other way round (`if (i >= 0) {min} else zero`)
    (465 -> 230; the plain half alone scores worse): `member_test_loop` (swap child). The local that was both the loop's
    carried value and assigned `i` kept a narrow register that had to be masked (`andi $v1,$v1,0xff`) and kept the
    guard of the loop a real compare; the target has the byte straight from the load, a constant guard
    (`move $v0,$zero; bnez $v0`) and the zero store placed after the other arm;
11. * one `do { } while (0)` round the third guarded scroll store (a first try round the whole straight-line stretch
    moved the loop bound copies above the halfword sum in global.c's order, 465 -> 425, with side effects on the local
    quantities of the following compare; round the last guarded store only, 270 -> 205): `store_guard_scope`. The sum's
    priority was `floor_log2(9) * 9 / 59 = 0.458` against the bound copies' 0.375 and its own sign-extended copy's
    0.429 (flow.c counts a reference once more for each loop it sits in); one more loop of weight on the compare's
    operands put the copy and the bounds first, which gave them `$a0` and the sum `$a1`;
12. * the byte scale read as `s32 scale = w->scale;` multiplied by the row height as it is and read through `(u8)` in the
    two later uses (`top - (u8) scale`, `(u8) scale * w->limit`) (200 -> 0): `byte_read_casts`. Every spelling that made
    the local a `u8` or `u16`, a second `t` local, or a multi-set temporary either folded the mask into a plain move
    (`move $a1,$v1`) or left the product on the masked copy; the casts at the reads keep the load's own register for the
    product and give the one `andi $a1,$v1,0xff` for the two later reads.

The recorded source relies on scaffolding: one `do { } while (0)` (step 11), the repeated expression of the clamp
(steps 3 and 4), the second byte local `m` (step 9) and a pointer-sum spelling of one row address
(`(u8 *)(i * 12) + (s32)w->rows`) that the last step made unnecessary (`&w->rows[i]` scores 0 as well). No profile
change. The library's release/pin workflows carry the families to the app: decomp-repair 0.5.797 and later.

Failed experiments that would otherwise be repeated:

- Weight ladders around single loops, around the loop bodies or around each statement (`do { } while (0)` ×1-2)
  moved the bound copies and the sum together and left the same swap (64 combinations of the six statements:
  only the third guarded store alone gave 205).
- `if (i >= 0) {min} else zero` alone, before the member test: the guard stops folding and the compare is masked, the
  frame hole comes back (592); the layout needs the byte to come straight from the load.
- Ternary spellings of the min (`selected < i ? selected : i`), `s32 selected`, `unsigned char`, a `selected`
  carried through a second `m` after the loop: 905-1137 or worse; the member as the loop test (step 10) is the fix.
- A `for (..)` over `u8 scale` with a `u8` or `u16` copy of the byte, `scale = t & 0xff`, `(u8)(t + 0)`, a second set of
  the temporary: all gave `move` or an extra `andi`; combine's `reg_nonzero_bits` is only recorded for pseudos set more
  than once, a copy inside one block sees the load's own extension.
- `prove_chain` with `--families all` on the saved best never finished a step in 40 minutes under load (the draft path
  generates ~700 children and one family is cut off at 120 s each step): calling the library modules directly on the
  current best (`variants(source, name, header)` of each module) and scoring every child with one judge worker scores
  all ~1000 modules in under three minutes, and it is how the library's own `assignment_at_use` and `narrow_widening`
  were shown to reach steps 5 and 8 (the matched source with that step undone, then the module's children).

What is controlled evidence and what is one function:

Controlled: every starred step was reproduced by calling its new family on the source before the step and scoring the
child (the measured scores above); `assignment_at_use` and `narrow_widening` reach the matched source from the state
before their step. One function: the priorities (0.458 / 0.429 / 0.375), the weights and the guard-fold behaviour are
this function's; the rules they illustrate (references count once more per loop in flow.c, a byte local assigned from a
wider value keeps its mask, a store through a base invalidates cse's cached loads of other members) are hypotheses
from the dumps, not proved from the compiler source.
