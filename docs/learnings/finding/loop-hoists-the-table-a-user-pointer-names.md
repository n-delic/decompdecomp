---
title: loop hoists the table a user pointer names
kind: finding
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mfp32
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Two N64 triangle walkers (a 438-instruction ray test and a 761-instruction shadow walker), SN64 GCC 2.8.1
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`. At the top of the loop each reads a vertex pointer and a
triangle pointer from two tables of pointers indexed by the same model number. In the target one table's address is
hoisted (its pseudo gets no register and reload rematerialises it in `$t0` every iteration) and the other's
`lui/addiu` stays in the loop; ours hoisted the wrong one, or got the order and then the registers wrong.

The rule (gcc-2.8.1 `loop.c`, `move_movables`): an invariant is moved only when `threshold * savings * lifetime >=
insn_count`, `threshold` starting at `(loop_has_call ? 1 : 2) * (1 + n_non_fixed_regs)` and dropping by 3 per move.
`&T[m]` and `T + m` expand the table address after the index load, so it lives 2 insns: `-dL` prints "not desirable"
for a 263-insn loop. An rvalue `T[m]` expands it first (life 3-4) and it is moved. Which table is hoisted therefore
follows how each statement is spelled and in which order, and the order the scheduler needs fights it.

The source change, and the measured result before and after (scores, the judge used):

A user pointer to the table that should be hoisted, set at the body's top, and one slot pointer per table with the
loads after both slots:

    tt = D_800F80E8;
    o = D_80140FF8[i].obj;
    ts = &tt[o->model];
    vs = &D_800FA860[o->model];
    tri = &(*ts)[D_80140FF8[i].tri];
    vtx = *vs;

App judge-worker under `-mfp32`: ray test 55 -> 0, shadow walker 195 -> 0 (and 90 -> 0 from its other saved best).
Both are matched in the app with the `-mfp32` per-function profile applied from an exact `compiler_experiments` trial.
decomp-repair `loop_table_slot_pointers` proposes it from the plain or the self-staged (`table_slot_self_stage`) forms.

Failed experiments that would otherwise be repeated:

- The table pointer alone with each pointer staged through itself: loop-head order exact, registers off (185).
- Slot pointers without the table pointer: 480; the triangle slot staged through `tri` with a vertex slot pointer: 615.
- A single-set vertex read (`vtx = *(Vtx16 **)&T[m]`, `vtx = *(T + m)`) beside the table pointer: 615 (its address
  is hoisted as well, or the order changes).
- Earlier: a 70-spelling grid of the two statements (rvalue, `&`, staged, every interleaving) stalled at 55.

What is controlled evidence and what is one function:

Two functions of one game, both with the same pair of tables; the loop.c threshold rule is from the compiler source and
the dump of each candidate. That the slot pointers fix the allocation through local-alloc's priorities is a hypothesis
the two measurements agree with.
