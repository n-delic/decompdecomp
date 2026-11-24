---
title: A 2,267-instruction collision sweep 195 -> 0: a cross-jumped tail copy and a break-as-return turn reload's spill-register rotation, a goto loop and a one-shot scope settle two priority ties
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The collision sweep of collision-sweep-retry-loop-walk-pointer-and-member-stores.md, left at 195 with register rows
only: from the u8 site's return block (`lw $t2, 0x19C($sp)` in the target) to the end the two GPR spill registers
`$t1`/`$t2` exchanged, and at the u16 object site the first product of the plane dot product in `$f4` where the
target (and the identical text at the u8 site) has `$f6`.

The source change, and the measured result before and after (scores, the judge used):

Standalone app judge (`fast_judge.py`), each new best submitted with `setup.py try`; the app records the match.

| edit | score | library family |
| --- | --- | --- |
| saved best | 195 | |
| the u8 site's closest-point arm, which ended `goto block_260;` (a label in the middle of the other arm, before the push tail), given its own copy of the tail (to its `goto block_269;`) | 1521 alone | `mid_label_run_copy` (new) |
| with it, the retry walk's `if (p == NULL) { break; } goto walk;` as `return (s8) flag;` | 30 | `loop_break_return`; offered together by `mid_label_run_copy` |
| the u16 triangle loop's `do { } while (..)` as a goto loop, like the u8 one | 1401 alone | `do_while_goto_loop` (new) |
| with it, the u16 clamp's `t = y + arg7; if (t < c) { c = t; }` in `do { } while (0);` | 0 | `parameter_read_scope` (new); offered together |

Why each step works (read in the 2.7.2 source and the `-dg`/`-dl` dumps):

- reload1.c `allocate_reload_reg` starts each search at `last_spill_reg + 1` over all four spill registers (`$t1`, `$t2`,
  `$f14`, `$f15`) and sets it to the one taken, in insn order; a GPR reload after `$f14` therefore always gets `$t1`.
  The target's return block took `$t2`, so its last reload before was `$t1`: one more turn than ours between the
  edge test's `arg6` reload and the return block, in code the final assembly does not show. The copied tail has three
  reloads (arg9, arg10, the flag's 1); jump2's cross-jumping deletes it after reload (the arm comes out as the target's
  jump into the shared tail), so only the rotation stays. Three turns flipped the walk at the end the wrong way; the
  return copy of the closing `lbu; move` (merged by jump2 too) turns it once more.
- The tail copy lengthened every loop-live pseudo by 60 insns, and the two float arguments `arg6`/`arg7` (31 refs,
  live 3458/3456) then tied in global.c: `floor_log2 (31) * 31 * 10000 / live` truncates to 358 for both and the lower
  pseudo (`arg6`) took `$f30`, the target's `arg7`. The return copy moved them to 3456/3454 (358 vs 359).
- local-alloc ties a dying input into the output's quantity and sums the refs; the dot product's first-product
  quantity (three pseudos) had 36 weighted refs at the u16 `do` loop's depth 6, `floor_log2` 5, against 30 (4) at the
  u8 goto loop's depth 5, so it outranked the load quantity there and took `$f4`. As a goto loop the u16 site weighs
  like the u8 one. That moved `arg6`/`arg7` to 29 refs, a tie again at 335; one more depth on one `arg7` read (30 refs:
  347) settles it.

Failed experiments that would otherwise be repeated:

- The `do { for (;;) } while (0)` wrapper around the object walk removed instead of the goto loop: the dot product
  matches too, but `arg6`/`arg7` tie (27 refs, needs four insns fewer): 1401.
- Code-preserving spellings to shift the two float arguments' live length by 2 (inverted float `if`s, `goto` forms,
  `!= 0` tests, boolean idioms, casts): only the u16 site's `if (dot > 0) goto ret; goto next;` order moved it (+2);
  the push tail's `(f & 0x7E0000) >> 17` (-4) changes the code.

Scaffolding: the match relies on one empty `do { } while (0)` around the u16 clamp's second step.

What is controlled evidence and what is one function:

One function. The rotation rule is read in reload1.c and counted in the `.greg` dump's reload insns; the global.c
and local-alloc priorities are computed from the `.lreg` refs and lives and the 2.7.2 formulas.
