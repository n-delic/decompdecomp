---
title: volatile cursors to guarded loops
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like and the exact compiler and flags: a 529-instruction N64 function (a recursive list walk
with a per-region triangle loop that projects vertices and keeps a screen box; `gcc-2.7.2-kmc -G0 -mips3 -mgp32
-mfp32 -O2`). Its saved best (922) kept the triangle loop's stepped cursors in `volatile s32` locals with a volatile
padding array, an identical-arm guard around the steps and around a pointer's assignment, an identity helper behind the
loop guard, and `do { } while (0)` wrappers around the loop nests. The app's judge recorded 0 for the hand match.

The source change, and the measured result (each score from the app's own judge through `fast_judge`; the chain is
what the families offer, greedy from the saved best, `decomp-repair` PR 1339):

| step | what changed | score |
|---|---|---|
| saved best | | 922 |
| `volatile_step_for_loop` | cursors written as the counter's multiples (`i * 3`, `i * 6`, the compiler's own giv steps, spilled by it: the frame's 8-byte hole in the spill area is the mult-2 giv the loop pass made for `i * 6` and dropped); the two identical `if (c) { A } else { A }` arms written once; `if (n > 0) do { } while (i < n)` as `for (i = 0; i < n; i++)` with the plain bound | 1263 -> 1486 -> 500 |
| `counter_offset_local` | `s32 offset; for (i = 0, offset = 0; ...; offset += 0x24, i++)` as `s32 offset = i * 0x24;` in the body | 500 -> 280 |
| `guarded_cursor_do_loop` | `for (i = 0; i < n; y += 9, i++, x += 9)` after the pointers' declarations as `i = 0; if (i < n) { y = ..; x = ..; do { ..; y += 9; i++; x += 9; } while (i < n); }` | 280 -> 160 |
| `load_add_in_place` | `p = ((u8 *) (*((void **) ..))) + i * 6;` as a load into `p` and an add on it, in both arms | 160 -> 105 |
| `sequential_cursor_split` | one cursor local walked by two list loops: a local per loop | 105 -> 50 |
| `scope_unwrap_rewrap` | the outer `do { } while (0)` removed, the plane-distance chain (store and early-out) given a scope of its own | 50 -> 10 |
| `adjacent_init_swap` | `void *a = NULL; void *b = NULL;` swapped | 10 -> 0 |

The compiler facts behind them (read in the source of GCC 2.7.2, except where marked):

 - A saved register goes to the pseudo with the highest `floor_log2 (refs) * refs / live length` (global.c; refs weighted
   by loop depth, flow.c). The loop counter had 19 weighted references and the argument copy 42/365 insns (priority
   0.575): the counter needed 23 to take the register before it. A `for` loop's copied entry test and the plain bound
   gave them; the strength-reduced multiples then ranked below both and were spilled, as the target has them.
 - reload.c gives a stack slot to every pseudo with references and no register, in regno order, and the loop pass leaves
   a stale pseudo for each duplicated loop test and each intermediate giv: removing a `for`'s copied test moved every
   later slot 8 bytes. The frame size and the slot of a spilled counter multiple follow from the loop's form.
 - local-alloc.c ranks a quantity by `floor_log2 (refs) * refs / life`, and an add whose first operand dies there shares
   its register: two float compares written the same way switched register order between loop depth 3 and 4 (the
   crossover of `floor_log2 (4d) * 4d / 3` and `floor_log2 (2d) * 2d`). One wrapper removed and the chain wrapped alone
   gave both compares and the chain their registers.
 - The order of the `move rN,$zero` insns of two NULL initialisers is the start of each live range; swapped, the
   register order of three pointers followed (hypothesis: the lengths, 12 vs 11 insns, were the only difference in the
   table `passes.py` prints).
 - loop.c puts the starts of the givs it makes after the copied entry test; pointers started in source before the
   `for` come before it, whatever the scheduler does. The same loop as a guarded do-while with the starts in the guard
   has the target's order.

Failed experiments that would otherwise be repeated: every `do { } while (0)` ladder on the counter's start (it raises
the multiples with it: they are stepped inside the same scope); `i * 0x24` written in each read (loop.c reduced the
symbol's address with it, 910); one load-and-add arm written alone (350, 405); the `for`-form with the guard's helper
(the copy of a test through an inline's block notes is not made); the cursors written as plain stepped locals
(`$fp` and `$s5` taken from the argument copy and the scratch pointer).

What is controlled evidence and what is one function: the chain is one function, measured step by step with the
judge; the rules above are read in the sources and each step's effect was seen in `cc1 -dl -dg -dL` dumps (priorities,
giv lists). The families' tests are synthetic sources of the same shapes.
