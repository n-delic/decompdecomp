---
title: collision sweep cursor fold frame and guard label
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 1928-instruction N64 collision sweep of `collision-sweep-call-runs-and-frame-scaffolding.md` (KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2`), handed over at 4707: an actor pass (ray tests over `u16` and `u8` index triangles)
and an object pass (two XZ walks, then two ray walks with a plane fallback and three `segmentClosestPoint` /
`distanceSquared3D` pairs each), then a tail that copies the hit out. Continued from the saved best, one edit at a time,
each scored with the app's judge standalone (`fast_judge.py`) and the bests submitted.

The source change, and the measured result before and after (scores, the judge used):

| step | score | family |
| --- | --- | --- |
| the grid mask as `temp = (u16) call(); temp = (u16) temp;` and a second, live set of `temp` in the next walk's mask test (`(m & w) && ((temp = hi & w) != 0)`, the ray caster's spelling) | 4707 -> 4687 | by hand (the ray caster's `birthing_insn_p` rule) |
| the object pass's ray walks: the m2c byte cursor `q = (char *) tri + 2` (reads `q + 0`, `q + 2`, `q += 6`) folded into `tri[1]`, `tri[2]` | 4687 -> 4192 | `cursor_lockstep_fold` (decomp-repair #1616: byte-addressed cursors, reads nested before the step, the fold that keeps the pointer's step; its best child is 4038) |
| frame: `spD8`/`spE0` as a union (`f32 d8[3]` over `{ f32 pad[2]; f32 e0[3]; }`), the two ray-hit distances as the first member of a struct with the plane/point array after them (`{ f32 hit; f32 unused; f32 plane[4]; }`) | 4192 -> 4563 (rows 242 -> 229, stack rows 97 -> 58) | by hand |
| each walk's hit distance copied once into a block local (`f32 distance = spF0; if (distance >= 0.0f) ...`) | 4563 -> 3863 | by hand (`member_read_local`'s idea; it does not see a member behind a macro) |
| the tail's `arg5->y = spFC + (spDC - spCC)`: the target reads `spFC` back from the frame | 3863 -> 3708 | by hand |
| the whole-body guard `if (arg0 != NULL) { ... }` as `if (arg0 == NULL) goto ret0;` with `ret0:` on the final `return 0` | 3708 -> 3188 | `guard_zero_return_label` (decomp-repair #1618) |
| `prove_chain.py --families all --children 300` from 3188 | 3188 -> 2973 -> 2863 -> 2823 -> 2808 -> 2798 | `mask_reread_pointer_stage`, `sentinel_constant_local`, `arm_variable_share`, `single_use_inline` |

Why the cursor fold works: loop.c's `combine_givs_p` lets the DEST_ADDR giv `tri + 4` serve `tri + 2` as `-2(reg)`,
so `tri[1]`/`tri[2]` give the target's one cursor register next to the biv `tri`. A user pointer of its own is a biv:
its zero-offset reads stay on it and the other offset (four reads) is reduced to a third register.

Failed experiments that would otherwise be repeated:

- The object pass's `&sp108`/`&sp118` (target: `$s6`/`$s5`, set once before the object loop): one pointer local each set
  there (5941; the `&sp108` pseudo, 50 refs over 1005 insns, ranks just below `&sp118` and finds no register), one
  local per walk set there (7527), plain `&sp108` everywhere (7125: loop.c moves it out of the outermost list loop
  too, `savings 1, life 1386`, and it is spilled), every segment pointer plain (7151), the outer list loop as a goto
  (12802). The per-walk locals of the saved best stay best so far.
- The u16 guard spellings: `i = 0; if (i < n)` with an `unsigned` counter folds to `blez` (3 stack rows but 4803 from
  4563); `if (flag < n)` straight: cse folds the known-zero flag (4603). The `guard = (flag < n)` form (identical code to
  a `u16` counter) keeps the target's `sltu $v0, $s2, $v0`.
- Struct-wrapped hit distances read through the member (no block copy): reloaded after the output stores, +370.
- `f32 hit[1]` arrays instead of the struct: a one-element array gets SFmode and is placed with the scalars (end of
  the locals), not at 0xf0.

What is controlled evidence and what is one function:

One function. The giv combination is read from loop.c (`combine_givs_p`) and the `-dL` dump; the loop-invariant
numbers above are from `-dL` of the variants. Remaining at 2798 (recorded): global.c's saved-register order in the actor pass
(cursor and counter swapped: `explain_regs.py`), the object pass's two hoisted pointers and the two spills the target
has there (hitIndex at 0x22c, `&sp168` at 0x224) that ours keeps in `$s6`/`$s4`, and the frame 0x10 short because of
those two spill slots. The union and the `unused` struct member are scaffolding the current best relies on.
