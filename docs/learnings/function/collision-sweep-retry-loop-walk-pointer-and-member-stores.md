---
title: A 2,267-instruction collision sweep 6792 -> 195: the retry walk as the outer do loop, one pointer for a walk's call argument, typed counters, struct-member stores, and scaffolding removed
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The collision sweep of collision-sweep-goto-else-arms.md (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, 2,267
instructions, frame 0x288): an actor walk, an object walk (m2c's `for (;;)`) over u16 and u8 triangle loops, a
result-pointer epilogue and a retry walk that jumps back to the top (`goto loop_3`). Saved best 6792 (scoring
version 2), m2c's draft tuned by searches.

The source change, and the measured result before and after (scores, the judge used):

Standalone app judge (`fast_judge.py`), every new best submitted with `setup.py try`. Each step measured against the
state before it.

| edit | score | library family |
| --- | --- | --- |
| saved best | 6792 | |
| the `u32` counter of the first object loop typed `u16` (cse's `make_regs_eqv` had compared the zeroed counter for the `flags & 4` guard; combine left a `(use)` of the dead compare that took an 8-byte slot: frame 0x290 -> 0x288) | 6591 | guard-counter idiom of sweep-guard-counter-and-call-temp-260-to-0.md |
| the retry walk as the function's outer `do { ... } while (obj != NULL)`, re-entering its own inner `do` (`if (!p) break; goto walk; next: p = p->next; if (!p) continue; walk: do { .. if (p != NULL) { if (!hit) goto next; p = p->next; break; } else { return (s8) flag; } .. } while (p);`), and `&spD0` passed to the object walk's calls read through one pointer local set at the walk's head (struct copies through it kept on `spD0`) | 6548 (outer loop alone 7228; with the pointer 4748) | `loop_address_argument_local_outer` (new, decomp-repair #1605: 7228 -> 4983) |
| the actor test's `do { } while (0)` scaffold removed (the actor pointer's weighted refs fell 136 -> 124 and arg3 took `$s4` again) | 4268 | |
| the hit tails' stores ordered `sp1BC = obj; gPlayerActor = actor; arg3->z += d; flag = 1;` (the target's tails share only the last float store; ours cross-jumped the global store too and lost two instructions) | 4158 | |
| `sp20F = 1;` before the walk's pointer local (reorg then fills the branch's slot with the `li`) | 4188 / with the next | |
| the first parameter's copy `sp194` without `volatile` and without the unused `volatile char pad;` (each alone 4730 / 13660 before: the pad only went once the copy was a pseudo) | 3678 | |
| the walk's first-pass flag `s8`, not `u8` (`lbu $t2; move $v0, $t2` before the test) | 3488 | |
| `sp194` dropped: the parameter itself read | 3248 | |
| the actor distance and the push factor one variable (`d = r - d`) | 3148 | |
| the first object's whole triangle block out of its `do { } while (0)` | 2728 | |
| the two counter-zeroing `do { } while (0)` fences and the `register` keywords dropped | 2728 | |
| one float for each first-section push factor (`f = D - sqrt(..)/r` and `f = r - d` in one local) | 2494 | `float_temp_merge` idiom |
| the push-out vector updates in x, y, z order (`sp18 = f * (..); sp1C = ..; sp20 = ..; p0 += sp18; ..`) | 2478, 2065 for the object walk's | |
| the u8 sites' hit tails in the same store order as the u16 ones | 2368 | |
| `(sp1DE & m) && (sp1E6 & m)` without the helper; each loop counter zeroed above its guard, after the tris read | 2193, 2073 | |
| the u16 object site's `goto block_258` (the return-1 block of the u8 site) as its own copy, `{ *arg3 = *start; return 1; }`: cross-jumping turns it back into the target's `bc1f next; j shared` | 1780 | `goto_arm_label_copy` reaches it |
| the plane offset added last (`dot + spFC`) | 1770 | |
| the object loops' heads as the sibling queries spell them: the cell mask read twice from its table and the element pointer staged `p = tris; p += i * 6;` (each alone worse: 1910, 2565) | 1210, 980 | `mask_reread_pointer_stage` (new, decomp-repair #1609: 1770 -> 980) |
| the clamp's `do { } while (0)` and 21 of the 22 `inline_fn2` and-helpers dropped | 980 | |
| the `arg3` field updates written as members, `((Vec3Copy *) arg3)->z += sp20;` (a member store is MEM_IN_STRUCT_P: the scheduler treats it as not aliasing the scalar global `gPlayerActor`, so the global's store no longer sits glued to the float store and the target's order comes) | 260 | `field_struct_view_printed` reaches it |
| the start-position copy `sp19C = arg2` dropped (the parameter read) | 260 | |
| the u16 site's return copy (1780 above) as `if (!(dot > 0.0f)) { goto next; } goto ret;`: the same `bc1f next; j ret`, without the copy's reload of the start position, which had moved reload's round robin by one | 205 | |
| the u8 site's mask tests with the mask first, `(m & sp21E) && (m & sp226)` | 195 | |

Left at 195: register rows only. The object walk's u16 site puts the first product of its plane dot product in `$f4`
where the target has `$f6` (local-alloc ties; the u8 site's identical text matches), and from the u8 site's return
block (`lw $t2, 0x19C($sp)` in the target, `$t1` in ours) to the end the two GPR spill registers are exchanged.
reload1.c `allocate_reload_reg` hands out the spill registers round robin from `last_spill_reg` over all four
(`$t1`, `$t2`, `$f14`, `$f15`, the `.greg` dump's "Spilling reg" lines), in the order reload meets the insns, so one
allocation more or less anywhere before flips every later choice; an inherited reload does not advance it. Ours
reloads arg4 (`$t1`), arg6 (`$f14`) and then the start position into `$t1` again; the target's start position is
`$t2`, so one reload in that stretch is inherited or absent in the original. List the reload insns (UID above the
function's last insn) between two divergent points in `.greg` to count them.

Failed experiments that would otherwise be repeated:

- The u8 triangle loop (a goto loop) as a `do { } while`: 1691 (from 260). The u16 loop as a goto loop: 1631.
- The object walk's `for (;;)` as a goto loop: 5463 (from 4748); the walk's `do { for (;;) } while (0)` unwrapped: 3523.
- The duplicated `temp > 0.0f` test at the end of the u8 site's edge condition removed: 2131 (it is the target's).
- The push tails (`block_196`/`block_260`) copied into both paths: 2656 / 2636 / 1991.
- `sp1AF = 1;` moved above the flags test in the object walk's tails: 1190.
- Statement orders of `gPlayerActor` / `sp1BC` / `sp1AF` / the float store at all four hit tails (both forms, every
  permutation, per site): nothing below the 4158 order until the member stores.
- The mask spelling at the u16 object site alone (helper, cast, operand order, index mask; 48 variants): no gain.

What is controlled evidence and what is one function:

All one function. The loop.c rule (a user variable set after a jump in a loop body is not moved out, `scan_loop`,
`maybe_never`), the `make_regs_eqv` frame hole and the MEM_IN_STRUCT_P scheduling rule are read in the 2.7.2 source;
the reload round-robin explanation for the `$t1`/`$t2` rows is a hypothesis.
