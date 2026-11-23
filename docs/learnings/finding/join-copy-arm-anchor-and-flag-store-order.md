---
title: a join copy that survives cse because an arm copies it too, and the arms' flag stores that follow their return guards (640 -> 0, decomp-repair join_copy_arm_anchor)
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

**Function and compiler.** A 148-instruction N64 object handler (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`): early
returns on two globals, a `switch (mode)` of five arms (two of them empty) that set a flag local (`force`) and may
return, a join that tests the flag (`near = force; if (!near && !(dist(..) < 3.0f)) return;`) and a second `switch`.
Saved best 495 in the app (640 in the isolated judge); the target was matched afterwards (0, recorded).

**What the target does.** The flag lives in `$s0`; at the join the target keeps m2c's copy (`move $v0,$s0; bnez $v0`),
the arm that came through the second label (`goto check`) has the copy in its `beq`'s delay slot and jumps past the join's
copy, and the `j`s of two other arms keep an empty slot (`j L; nop`), their flag store sitting in the preceding
`beqz`'s slot. The first rows (`lh $v1,0x7c($s1)` with the multiply in `$v0`) come from reusing the entry index local
for the unrelated later reads (`scalar_storage_reuse` already offers that).

**Rules read in the compiler's source and reproduced with cc1's dumps (`-dr -dj -ds -dL -dg -dS -dR -dd`, and lldb on
cc1 with a breakpoint on reorg's `fill_slots_from_thread` / `eligible_for_delay`).**

1. *cse deletes a join copy whose destination is unreferenced above the join.* `make_regs_eqv` makes the new register
   the canonical one of its quantity only when its life reaches past the extended block or starts before it. `near`
   referenced first at the join keeps the older `force` canonical: the test reads `force`, the copy dies, and the
   output has `bnez $s0`. A copy in the arm that jumps to `check` is a reference above the join block, so `near` is
   canonical and the join's copy survives (the `.cse` dump shows insn 201 `near = force` deleted in the one case and
   kept in the other). A dead `near = 0;` at the top does the same without a program; the arm copy is the program the
   target's layout reads as (`near = force; goto check;`, `check:` after the join's copy).
2. *reorg fills each arm's `j` from the join's first insn, and each guard's `beqz` steals the flag store out of the `j`
   in front of it* (`fill_simple_delay_slots`, `fill_eager_delay_slots`, `relax_delay_slots`, two passes; lldb shows
   `steal_delay_list_from_fallthrough` taking the store in pass 1 for one arm, and the `j` refilled with the join's copy
   in pass 2). Which `j`s end empty depends on where each arm's flag store sits relative to its guard: m2c's
   `force = 1; if (!ready) { return; }` (store before the guard) left two `j`s with the join's copy in their slots (700
   with only the anchor), the natural `if (!ready) { return; } force = 1;` gave the target. This part is read from the
   traces of one function, not from a controlled experiment: label it a hypothesis.
3. *Neither edit alone.* Knob table on this function (judge scores; the fixed arm is case 1, which already stored after
   its guard): anchor alone 700; anchor + store sunk in one of the two arms 350; anchor + both 0; both stores sunk
   without the anchor 1150 (the copy folded away). Unused `goto joined` before the label stays and changes nothing.

**What was measured (isolated judge, then the app).** Saved best 640; `scalar_storage_reuse` alone 605; with the anchor
and both stores 0; the app's `compile_and_diff` recorded the match (148 instructions, byte-identical). From the saved
best `prove_chain.py --families all` reaches 0 in two steps (`join_copy_arm_anchor_sink` 640 -> 35, `scalar_storage_reuse`
-> 0). Library: decomp-repair PR 1320, `join_copy_arm_anchor.py` (declares itself with `REPAIR`; children
`join_copy_arm_anchor` and `join_copy_arm_anchor_sink`).

**Failed or neutral.** `(u32)` cast on the second switch (the chain's first pick, 445): adds an `andi` the target does not
have. `near = 0;` at the top: same copy, but a dead store. `register`, `s16`/`u8` `near`, a chained second copy, `break` for
`goto joined`, computing `bonus` inside the call: no change or worse. Moving `bonus = obj + 0x24` above the join copy
makes it live across the call (frame +8, 1595).

**Evidence level.** One function, verified by the app's judge; rule 1 reproduced in the dumps; rule 2 observed in lldb
traces of one function and tested by the knob table above only.
