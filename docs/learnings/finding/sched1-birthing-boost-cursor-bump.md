---
title: sched1 birthing boost cursor bump
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

The source change, and the measured result before and after (scores, the judge used):

Failed experiments that would otherwise be repeated:

What is controlled evidence and what is one function:

(Filled in below.)

**Function and compiler.** A 183-instruction N64 draw function (a loop emitting a matrix command), `gcc-2.7.2-kmc`,
`-G0 -mips3 -mgp32 -mfp32 -O2`. One difference left (60): in the arm after a call, the target has `lw cursor; lw slot;
addiu v0,cursor,8; subu v1,slot,s5; sw v0,cursor_global`, ours had the `subu` before the `addiu`.

**Cause (sched.c, read; the trace confirms).** sched1 schedules a block backwards. `schedule_insn` calls
`adjust_priority` on each predecessor that becomes ready; with no REG_DEAD notes (always, there) an insn for which
`birthing_insn_p` holds (sets a pseudo that is live afterwards and has `reg_n_sets == 1`) gets `max_priority`, i.e.
the launch priority. The `-dS` trace showed `339 (7f000001)` (the `addiu`, single-set pseudo) picked ahead of `351`
(the `subu` on the multi-set user variable `sel`, priority 5) the moment its store was scheduled, so the `addiu`
was placed right before the store and the `subu` first. Statement order does not matter: all five orders of the
arm's statements scored 60 or worse.

**Fix.** `D_cursor = dl + 1;` written as `cursor_next = dl + 1; D_cursor = cursor_next;` with one temporary assigned
in BOTH arms (two sets: not birthing). Either arm alone: 60; both: 0. decomp-repair `arm_increment_temp` (0.5.629),
proved from the saved best by the library function alone, wired into the draft repair path in the same PR as this note.

**Failed experiments.** All permutations of the arm's five statements; the store written into each arm; direct
`dl->w1 = slot - 0x80000000` forms (these change which constants the loop hoists first); a second temporary for the
pointer (`dl2`); `do { } while (0)` wrappers around parts of the arm or the call; post-increment; `nx` in one arm only.

**Evidence level.** One function, with the compiler's source and the ready-list trace as the mechanism; the
sched.c reading is from the community reconstruction of 2.7.2, not KMC's own source.
