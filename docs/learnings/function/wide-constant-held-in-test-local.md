---
title: A wide constant held in the earlier test's local and three volatile pins (190 -> 70)
kind: function
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:
A 72-instruction N64 object update, `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64` (the
function's profile; -mfp32 only renames one float register). After `if (D->f != 4) return 0;` the block computes
`obj->x10 = p->x10 + 0x8000` beside two stores through `p` and a call's argument setup. The target builds the
constant early (`ori $v1, $zero, 0x8000` after `a1`'s address), in `$v1`, the register the test had loaded; every
spelling put the `ori` right above its `addu`.

The source change, and the measured result before and after (scores, the judge used):
- sched.c `birthing_insn_p` launches an insn whose destination pseudo is set exactly once (and live) as soon as it is
  ready; a literal constant is such a pseudo. One local for the test's operand and the constant
  (`k = D->f; if (k != 4) return 0; ... k = 0x8000; ... + k`) gives it two sets.
- Alone that is worse: without the `ori` filling the load's latency, sched1 lifts the load of `p->x10` above the two
  stores through `p` (`memrefs_conflict_p` proves same-base, other-offset references disjoint). Marking the load and
  both stores `volatile` (sched.c's `true_dependence` orders two references only when both are volatile) keeps them.
- Together: 190 -> 70 in the app (rev 4accfa3a); each half alone 485 / 1050 on the isolated scale where the saved best
  is 485 (the isolated worker links the `4500.0f` literal under another symbol, +300 over the app). decomp-repair
  `constant_compare_temp_reuse` offers both children; the `volatile` is scaffolding.

Failed experiments that would otherwise be repeated:
Left: the `ori` sits above the `move $a0`/`a1` setup instead of after it. sched1 places it at the very top because
priority-1 ties are sorted by INSN_LUID (`rank_for_schedule`) and the argument moves into `$a0`..`$a2` are emitted at the
call, with larger LUIDs; sched2 then breaks its ties by the sched1 position again. No change (isolated 365): all orders
of the six block statements, `k = 0x8000` at five places, the arguments through locals (three places), `&D_` forms, the
call result in a local. The call argument `t` reused for the constant 1040, `k` reused from the `0x1E` store 1100, from
`0xC8` 580, `k` reused for the later function-pointer store 1155; one or two of the three pins 900-1095.

What is controlled evidence and what is one function:
One function; the scheduler rules are read in gcc-2.8.1's sched.c and alias.c.
