---
title: bit stream decoder guard pointers and loop made registers
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

The 681-instruction N64 bit-stream decompressor of `bit-stream-decoder-subtable-pointer-and-loop-roles.md` (KMC GCC
2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -fno-schedule-insns`), resumed at the saved 290 (58 rows). The rows the earlier
entries could not move: the target loads several table addresses fresh inside guarded blocks (`lui $v1,%hi(T)` for the
code-assignment pass's inner pointer, `lui $v0,%hi(H)` before each later pass's bound `addu $s0,$t7,$v0`), where every
spelling gave a copy hoisted to the entry, spilled and rematerialised into the reload register `$s7`. Every kept source
was built on the host beside the 290 source and run on 1200 generated streams (stored and coded blocks, codes past the
first-level widths, matches with extra bits, the reuse-the-tables path, random tails); output, error word and all
tables compared. The harness caught a deliberately wrong second-level step on 438 of 600 streams.

The source change, and the measured result before and after (the app's judge-worker; each new best confirmed by
`compile_and_diff`):

| step | edit | score | family |
|---|---|---|---|
| 1 | the code-assignment pass's inner test through a pointer its guard sets: `u32 *tp = T;` after `u32 hb = 1 << c;`, `while (tp[k] == c)` | 290 -> 275 | `guard_symbol_pointer` (new, decomp-repair #1607) |
| 2 | the three later passes' bounds through one function-level pointer set in each guard: `u32 *lim; gp = H_sym; lim = (u32 *)((char *)gp + off);` (the first pass's `H + c` left alone) | 275 -> 235 | `guard_alias_pointer` (#1607) |
| 3 | the code-assignment pass's cursor and byte offset dropped: `if (T[k] == c)`, `while (*(u32 *)((char *)T + (k << 2)) != 0)` | 235 -> 230 | `stepped_cursor_index` (new, #1608) |
| 4 | the code-assignment pass's guard and do-while as the `while (T[k] == c) { y = i \| (1 << c); .. }` it was (no `hb`, no guard pointer) | 230 -> 215 | `guarded_do_while_loop` (new, #1611) |
| 5 | the later passes as `for (i = 1; i <= H_sym[c]; i++, k++)` (no `lim`, `off` or shared pointer), the length pass's outer loop as `for (c = 1; c < tb; c++)`, the code-assignment pass's outer loop as `while (*(u32 *)((char *)T + (k << 2)) != 0)` | 215 (same code, natural source) | |

Why (read from the 2.7.2 source, `loop.c`): `scan_loop` moves a constant's set out of a loop only when it is used in
its own basic block, or is not a user variable, or is reached on every iteration and not used before it; it never moves
a register made by loop optimisation of an inner loop (`REGNO >= max_reg_before_loop`). A table address written in the
C is a temporary: every enclosing loop moves it, `combine_movables` merges it with the other loads of the symbol
(`done move-insn matches` in `-dL`) and it ends at the entry. A user pointer set in a guard (`maybe_never`) and used in
the loop after it is not movable, so its `la` stays in the guard, and the giv the strength reduction derives from it
(`T + 4k`) starts there too. One pointer set in three guards is set more than once in the outer loops and is never
moved; the first pass's `H + c` must stay on the hoisted alias (sharing it there too was 905). Step 3: the reduced
registers' increments are emitted at the counter's own increment (`emit_iv_add_mult` before the biv's insn), after the
inner loop's givs, which is the target's order; source cursors are incremented where they are written. The exit
test's `lw %lo(T)(reg)` needs the offset to be a register when the address is expanded: `(k << 2)` is a shift insn,
`k * 4` and `T[k]` are a multiply that `memory_address` forces through a temporary holding `T`.

Failed experiments that would otherwise be repeated:

- The symbol instead of the alias everywhere (`H` dropped): 650; every subset of the seven `H` sites by `H` or the
  symbol (128 compiles): 235 at best, eight distinct sets of instructions.
- The guard-local pointer declared before `hb` (395), a guard-local histogram pointer per pass (245-1045), the bound
  read through the pointer in the inner test instead of a `lim` local (810-1015 for the last pass: the pointer then
  lives across the loop and takes `$fp`).
- Step 3's cursor without the offset (245) or the offset without the cursor (450), the guard as a byte-offset read too
  (1225), the three increments in every order (235-250).
- Role renames in the first two passes (the clear and length loops' counter as `i`, the sort count as `k`, the sort
  loop's length as `i` or a variable of its own), alone and in every combination: 585-1520.
- The decode pass's mask as `(1 << tb) - 1`: 310 (lifts `tb` past the saved count again; a `do { } while (0)` on the
  last pass's pointer to rebalance it: 360).

Steps 4 and 5 are the same rule seen from the source the developer wrote: jump.c copies a `while`/`for` test in front
of the loop and loop.c moves the loop's invariants into the preheader after that copy, so the fresh loads sit in the
"guard" without any pointer local; steps 1-3 were stand-ins that the natural loops replace (step 2's shared pointer is
gone at 215). The same rewrite of the passes' outer `if (c < 16) { do { } while (c < 16); }` as `for (; c < 16; c++)`
was worse (335-650, the frame), as was the decode-table pass's do-while as a `for` (515).

Still different at 230 (47 rows; at 215, 43: the code-assignment pass's inner pointer now loads fresh): the first passes' counter (`$t3` for `$a3`) and the histogram increment's
`$v0`/`$v1`; the sort loop's length and pointer (`$a0`/`$a3`) and count (`$a2` for `$t3`); the code-assignment pass's
inner pointer from the hoisted copy again (step 3's guard read `T[k]` is a temporary that cse shares with the
guard's pointer); the last pass's `off` in `$s0` for `$t9` and its inner pointers (`$t5`-`$t9`, `$s0`).

What is controlled evidence and what is one function:

One function. Each step's family reproduces the step from the source before it (tests in decomp-repair) and every kept
source passed the 1200-stream check. The loop.c rules are read from the compiler source and agree with the `-dL` dumps;
that they are what the original source did is this function's evidence only.
