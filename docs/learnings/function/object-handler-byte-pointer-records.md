---
title: An object handler read through raw byte-pointer casts - typed records, a literal and the callee's arity together
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: observation
date: 2026-11-26
families: byte_pointer_record_view, callee_arity_trim
---

What the function (or case) looked like and the exact compiler and flags:

A 72-instruction SN64 GCC 2.8.1 object state handler of an N64 game (the project's profile with `-mfp32` per
function; under `-mfp64` the float temporary is `$f1` where the target has `$f2`, 10). It sets the object's state,
copies the player's angle, calls a lookup, then writes into the player record (`p->link = obj; p->y = obj->y - 4500.0f`)
and sets `obj->ang = p->ang + 0x8000` before two more calls. The saved best stayed at 70 through a dozen sessions
(docs/learnings/experiment/sn281-constant-placement-session-two.md): it read the object parameter (`u8 *obj`) and the
player pointer (`extern u8 *`, copied to a local) through `*(T *) (p + K)` casts, with three `volatile` pins that kept
the load of `p + 0x10` below the two stores through `p`, a constant local (`k = 0x8000; ... + k`) and a third call
argument the callee's matched C does not take. The sessions read the last rows as a sched1 birth/LUID question about the
`ori $v1, $zero, 0x8000`.

A rewrite from the target's assembly with the pointers typed as records (members at 0xC `f32`, 0x10 `s32`, 0x64, 0x88,
0xA8 the linked object, 0xD8 the state function; the game-state pointer a record with the mode at 0x7C), no pins, the
literal `0x8000` and the callee with two arguments matched at once. As members the accesses are structure references,
and the scheduler kept the `p + 0x10` load below the stores with no pin; the `ori` then landed where the target has it.
That reading (structure references versus casts through a char pointer) is a hypothesis taken from the target, not
read in the compiler's source.

The source change, and the measured result before and after (scores, the judge used):

`fast_judge.py` (the app's judge-worker) under the `-mfp32` profile; the match confirmed in the app (trial applied
with `suggest`, recorded matched).

| From the saved best | Score |
| --- | --- |
| saved best | 60 |
| records only (pins kept, `k` kept) | 60 |
| literal only (raw casts) | 180 |
| records + literal, third argument kept | 180 |
| records, pins dropped, `k` kept | 745 |
| third argument dropped alone | 60 |
| argument dropped + records + literal (pins kept or dropped) | 0 |

The natural rewrite's statement order mattered too: `p->y = ...` before `p->link = obj` (0), or the float difference
computed into a local before both stores (0); `p->link` first with the difference inline was 280.

Failed experiments that would otherwise be repeated:

Every store order and constant placement in the raw-cast form (the earlier sessions' notes), `k` at 16 positions, the
call's address arguments in locals, the comma form in a call argument, every installed SN/KMC/gcc-2.8.1 profile: none
below 60 while the pointers stayed `u8 *`.

What is controlled evidence and what is one function:

One function. decomp-repair `byte_pointer_record_view` reproduces it from the saved best (its child on
`callee_arity_trim`'s trimmed source scores 0); run over the project's 2,114 saved sources it fires on 23. Whether
retyping helps elsewhere is for the judge: the family's children keep the pins in one pair and drop them in the other.
