---
title: A constant the target schedules or allocates as if it were not a single-set block-local pseudo
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Three unmatched functions of one SN64 GCC 2.8.1 N64 game (profile above) stall on the same kind of row, after many
agents' hours each: a constant built by one `li`/`ori` sits where a single-set, block-local pseudo cannot.

- A 72-instruction object state handler (saved 70): `ori $v1,$zero,0x8000` for `obj->x10 = p->x10 + 0x8000` sits
  after the next call's a0/a1 setup in the target; ours has it right above the `addu` (a literal) or at the very top
  of the block (a twice-set local).
- A 37-instruction init routine (saved 200): the `li 0xFF` stored to an array element and the array's `%hi` are not
  placed beside their stores and take later registers (`a0`, `a1`).
- A 262-instruction display-list builder (saved 470): the vertex colour 0xFF, 16 byte stores, is allocated after
  every other value in its block (`$t6`, after the Gfx-word constants in `$t2..$t4`); ours gives it `$v1`.

Mechanisms read from the compiler's source and dumps (`passes.py --save`, `--sched-verbose`):

- sched.c `adjust_priority` boosts an insn to `max_priority` when `birthing_insn_p`: its destination is live below
  and `REG_N_SETS == 1`. A plain literal's `li` is therefore launched as soon as its user is scheduled (backward), so
  it lands next to that user.
- Without the boost (a local set twice) the constant has priority 1 and loses every tie (`rank_for_schedule`:
  priority, then dependence class on the last scheduled insn, then the larger `INSN_LUID` first), so it lands at the
  top of the block. For the object handler an exact simulation of sched2 from the sched1 order shows the target's order
  comes out iff the constant's set lies in sched1's output after the a1 `lo_sum` (insn 126) and before the a2 `HIGH`
  (insn 121). Neither form gives that: it needs either a LUID between the call's argument loads or priority >= 2 with
  a successor scheduled after insn 121.
- local-alloc.c only takes a pseudo with `REG_BASIC_BLOCK >= 0 && REG_N_DEATHS == 1` (line 472). The colour constant
  has 17 refs over 74 insns (priority ~0.92); the Gfx-word constants the target allocates before it rank 0.03-0.08, so
  no block-local pseudo with 16 refs can come after them: in the original the colour was global (read in two blocks
  without crossing a call) or died twice.

The source change, and the measured result before and after (scores, the judge used):

None matched. Scores below are `fast_judge.py` with the decompdecomp PR 3640 judge (the literal-pool vote fixed;
before it the isolated and the app's judge added 300 to any source with a float literal near a much-referenced global).

Failed experiments that would otherwise be repeated:

- Object handler: `prove_chain --families all --children 120 --fp-flip --combine 8` (library 0.6.59): nothing.
  `-mfp32` fixes its `$f1`/`$f2` row only (60). The store as a comma expression inside the call's third argument:
  no change (arguments are precomputed before the hard-register loads). `u16 k = 0x8000`: same as the literal.
- Init routine: all 2520 orders of its ten stores with the five `lui $v0`/`sw` pairs in the target's order, the
  array-element chains in order and `D_800CD0E0 = D_800BD6C0` last: best 230 (the saved order, with that copy early,
  is 200). Volatile externs in the target's order: 1040. gcc-2.8.1-pm and gcc-2.8.1-pm-sn-as: 4420;
  sn-n64-272-970404: 1325; `-fno-schedule-insns`: 1115.
- Display-list builder: a colour local (s32, u8, u32) set once or twice in the vertex block: 740 (cse deletes the
  redundant second set); the same local also passed as `func(0, 0, 0, c)` earlier: 740 (cse propagates 255 into the
  argument copy and flow deletes the first set, so the local is block-local again).

What is controlled evidence and what is one function:

Observation on three functions of one game; the pass rules are read from gcc-2.8.1 source and confirmed in the SN
build's `-da` dumps. Whether the original sources had such a constant in a variable read in two blocks (a shape these
straight-line functions do not show) or the build behaved differently is not established.
