---
title: A 1337-instruction collision sweep, 390 -> 0: an s8 flag's paradoxical subreg, a break as the closing return, loop scopes that reweigh refs
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The collision sweep of `function/collision-sweep-walk-reentry-flag-share-5207-to-390.md` (KMC GCC 2.7.2, `-G0 -mips3
-mgp32 -mfp32 -O2`), left at 390 (30 rows): `arg6`/`arg7` in `$f30`/`$f28` swapped, the 0x48 flags byte read into the
counter's saved register, one float sum's registers in the first u16 loop, the two tests of the re-scan flag lacking
the target's `lbu $t2; move $v0, $t2`, and the return value's byte loads with `$t1`/`$t2` swapped.

The source change, and the measured result before and after (scores, the judge used):

App judge through `fast_judge.py`, each new best submitted with `setup.py try`; the app recorded the match.

| edit | score | library family |
| --- | --- | --- |
| saved best | 390 | |
| the re-scan flag (only ever 0 or 1) declared `s8` instead of `u8` | 180 | `flag_signed_byte` |
| `if (cell == NULL) { break; } goto walk;` with `return (s8) hit;` for the `break` (the break left for that return) | 110 | `loop_break_return` |
| the u16 loop's hit arm (after `do { hit = f(..); } while (0); if ((hit << 0x18) != 0)`) as `{ do { .. } while (0); }` | 10 | `call_arm_scope` |
| the flags byte read straight into the mask (`t = p->flags & 4;`, not into the counter first) | 90 alone | `flag_read_split` |
| with it, the u16 goto loop's last two steps and test as `do { base += 3; i += 1; if (i < n) goto rows; } while (0);` | 0 | `loop_tail_scope` |

Why each step works (read in the 2.7.2 source and the `-dc`/`-dg`/`-dl` dumps):

- An `s8` flag's tests sign-extend it. combine knows the pseudo's nonzero bits (it is only set to 0 and 1) and turns
  the extension into a paradoxical `(subreg:SI (reg:QI flag) 0)`; find_reloads forces a reload for a paradoxical
  subreg of a value that extends when loaded (`LOAD_EXTEND_OP`), so the spilled flag goes into a spill register and is
  copied (`lbu $t2; move $v0, $t2`). A `u8` flag's `zero_extend` takes the stack slot directly (`lbu $v0`), and no
  spelling of the test (`!x`, `(u8)`, `(s32)`, `& 0xFF`, copies) changes that: the declared type does.
- reload1.c `allocate_reload_reg` rotates over the spill registers (`last_spill_reg`) one step per reload, in insn
  order. The extra `return` before the walk is a copy of the closing return's `lbu; move` that jump2 later merges, but
  it was reloaded first, turning the rotation once more, so the walk's hoisted `lbu` and the closing one swap
  `$t1`/`$t2` as the target has them. The shorter path also ends `arg6`'s life two insns earlier, and global.c's
  `floor_log2 (refs) * refs / live` then ranks it as the target does (157 vs 158 before).
- flow weighs each reference by the loop depth, and `do { } while (0)` emits loop notes. Inside the hit arm, the sum
  `*p = *p + v[i]` loads tie in local-alloc's quantity order (`floor_log2 (refs) * refs / life`) the other way, so the
  load of `*p` takes `$f2` as in the sibling u8 loop (a real `do`/`while`, already one loop deeper), and sched2 keeps
  the flag store where the target has it. On the loop tail it lifts the counter (refs 34 -> 36, priority 0.639 ->
  0.677) above the u8 loop's pointer base (0.652); wrapping only `i += 1` gave the registers but let sched1 move the
  bound's `lhu` after the step (60): the test has to be inside the same scope.

Failed experiments that would otherwise be repeated:

- The flag `s32` (word loads), `(s8)`/`(s16)` casts on a `u8` flag, a `u8` copy: no `move`. An `s8` copy of a `u8` flag
  also gives the `move`, but the declaration is the simpler program.
- The float sums: `+=`, operands swapped (130), the third sum moved up, a pointer local: no change; the scope around
  only the sums (70, the flag store then moves), around the sums from the call (300), around the vector updates (1395).
- The flags byte in a local of its own, without the tail scope (90-150 at each stage): the counter falls below the
  pointer base. The counter `s32`/`u32` (507-567), its zeroing inside the guards (190-485, the delay slot is lost), the
  two pointer bases as one local (90-160), the guard compared through the counter (4327), the u16 goto loop as a real
  `do`/`while` with or without the pointer step in both arms (3340-5997), the vertex index read before the call scope.

Scaffolding: the match relies on three empty `do { } while (0);` scopes (the earlier call scope and the two above).

What is controlled evidence and what is one function:

One function. The paradoxical subreg, the reload rotation and the loop-depth weighting are read in the compiler
source and seen in the dumps; the priority numbers come from `alloc_roles.py` on this function.
