---
title: A load that must not be boosted by sched1, without giving the entry test its register
kind: experiment
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2 -mfp32
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:
A 234-instruction N64 scene init at 10 (`sn-n64-281`, `-mfp32`): `id = D_X; ...; arg0->f = id + 470;
D_X = id + 1; call(.., id + 1)`, target `lw $a3; ... addiu $a3, $a3, 1` with the `lui $t0, %hi(D_X)` at the top of
the block, and the entry test `lw $v0, 0x7C($s0); bnez $v0`.

The source change, and the measured result before and after (scores, the judge used):
Unsolved. 2.8.1 sched.c `birthing_insn_p` lifts a set-once live pseudo to the top priority as soon as it is ready:
with `id` set once the `lw` is launched next to its users and the `lui` follows it down (210). With `id` also the
entry test (`id = arg0->unk7C; if (id == 0)`), `REG_N_SETS (id) == 2`, the `lw` keeps the target's place (10), but
`id` is one global pseudo and the entry test lands in `$a3` instead of `$v0`. `T = id + 1` must stay its own
(birthing) pseudo: written as `id++` / `++id` / `id += 1` / `D_X = ++id` the increment is not boosted and loses
T-1 to the `a0` argument move by INSN_LUID (560).

Failed experiments that would otherwise be repeated:
A second set of `id` that feeds only a call argument (`id = o->z` before a call, `id = 0` arguments, an unused
fourth parameter): combine folds it and decrements REG_N_SETS (`try_combine`), 210. The loop counter or an angle
local reused for `id` (saved register, crosses calls), `func_80023D58`'s result (`move` before the store),
`s32 *q = &D_X` (90), volatile reads, -G8.

What is controlled evidence and what is one function:
One function. Next: a second set of `id` that survives flow and combine and sits where the target already uses `$a3`.
