---
title: A 1337-instruction collision sweep, 5207 -> 390: a giv killed by a second biv step, the walk re-entering its own loop, guards through one flag local
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The collision sweep of `function/collision-sweep-frame-union-goto-tails.md` (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32
-O2`): an outer loop over actor lists, a u16 and a u8 triangle loop per section, a plane push-out per triangle, a closing
walk over a cell list. Saved best 5207 from the previous hand-off; its sibling `sweep-guard-counter-and-call-temp-260-to-0`
had just matched.

The source change, and the measured result before and after (scores, the judge used):

App judge through `fast_judge.py`, every new best submitted with `setup.py try`.

| edit | score | library family |
| --- | --- | --- |
| saved best | 5207 | |
| the u16 triangle call tested through `do { hit = f(..); } while (0); if ((hit << 0x18) != 0)`: its argument temporaries weigh one loop deeper, the base pointer outranks the second index in local-alloc and takes `$v1`, reload's spill registers become `$t1`/`$t2` | 5162 | `condition_call_scope` |
| m2c's mask local `sp166` reused for the u8 loop's vertex base split off (the mask test of the next iteration read the base: not the same program) | 4906 | `loop_reread_split` |
| the u8 loop's `p += 3` in both arms of its `if`, the cell walk as a real loop, the low half of the cell mask pair `u16` (together; alone 7413, 5551, 5225) | 3236 | `saved_register_cascade` |
| the walk re-entering a do/while of its own (`goto walk; next: p = p->next; if (!p) continue; walk: do { .. if (p != NULL) { if (!t) goto next; p = p->next; break; } else { return (s8) hit; } .. } while (p);`) | 2065 from 4906 with the step above | `walk_cascade`, `goto_walk_reentry` |
| the second section's per-triangle pointer: base loaded first, offset added after the index copy | 2516 / 1345 | `pointer_base_stage` |
| the locals of one saved register (`var_s3`, `var_s3_2`, `temp_s3`) as one variable, the else loop's `u16` counter as the first loop's masked word counter | 2218 / 1053 | `sibling_loop_merge` |
| the vector read `y = v[1]` above the two struct copies out of `v` | 833 | `scalar_read_before_copy` |
| the second section's `& 4` tests through the first section's flag local, every guarded counter zeroed above its guard as `u16` | 560 | `guard_flag_share` |
| one float temporary for the four push-out sites (m2c's `temp_fa0`, `temp_fa0_3` and a `d = r - d` reuse) | 475 | `float_temp_merge` |
| the second section's u16 row index in a local of its own (m2c reused `temp_v1_16` in the u8 loop) and that loop's base staged like the first | 450 | by hand |
| the u16 loop's steps as `p += 6; base += 3; i += 1;` (counter last) | 390 | by hand |

Why the main steps work (read in the 2.7.2 source):

- loop.c `strength_reduce` subtracts `add_cost * biv_count` from each giv's benefit. With one `p += 3` the two
  `*(p - 1)` loads combine into one giv worth reducing (benefit 4 against a threshold the `-dL` dump puts at 252 for a
  148-insn loop), so it takes a saved register of its own and every later saved register shifts; with a second
  increment the benefit is 0 and the loads stay `-1($s3)`. jump2 cross-jumps the two adds back into one.
- `find_and_verify_loops` moves a block that ends in a jump out of the loop (to a barrier near the jump's target) when
  a conditional jump to the insn after it precedes it. `if (p == NULL) return x;` inside the walk is such a block and
  was moved next to the function's first `return`; in an `else` arm the preceding jump is unconditional and it stays.
  Inside the walk's own do/while, loop.c then hoists the `sll` of the return value's sign extension into the
  preheader, which the re-entry jumps to (the target's `j` past `next`).
- cse's `make_regs_eqv` heads a class of equal registers with the one whose last use is latest. A guard `flag < n`
  in an arm where a word counter was just zeroed, or where a pointer local holds a known 0, compares the counter or
  the pointer instead (combine then folds `0 < n` and leaves a `(use)` of the dead compare result, which got an 8-byte
  stack slot). The flag local used again in the next section outlives both, and a `u16` counter is an HImode pseudo
  outside the SImode class.

Failed experiments that would otherwise be repeated:

- The u8 loop as a goto loop (no loop.c giv): 7611, the frame loses 8 bytes. Dummy loop insns only to read the
  threshold (diagnostic). `var_s3 += 1; var_s3 += 2;` also kills the giv but scored worse than the arm copies.
- The walk as `for (;;)` with one `next` site, as nested real loops, as `do`/`while` with the tail shared, as a goto
  walk with the return outside the inner loop: 7668, 7853, 8298, 3997.
- Return type `s32` with `(s8)` only in the walk: identical code until the return sat in an `else` arm.
- `sp147` tested as `!x`, `(u8) x`, `(s32) x`, a `u8` or `s32` copy: identical code (cse forwards them); the
  target's `lbu $t2; move $v0, $t2` before the two tests is still missing.
- `sp166` as `u16` before the cascade (5225), the u8 counter shared without masks (2828), the byte flag read into a
  call temporary or straight into the flag (690-920 at 475: the counter then loses the saved register to the u8
  base by 0.639 against 0.652).

Left at 390 (30 rows): `arg6`/`arg7` in `$f30`/`$f28` swapped (global.c priorities 157 vs 158 after integer
truncation: arg6's live length is 2 insns longer), the 0x48 byte read into the counter (`lbu $s4` for `lbu $v0`),
one float sum's registers and load order in the first u16 loop, and the two `sp147` tests' reload move (which also
shifts two branch encodings).

What is controlled evidence and what is one function:

One function. The loop.c, cse and find_and_verify_loops rules are read in the compiler source and seen in the dumps;
the threshold numbers come from `-dL` on this function.
