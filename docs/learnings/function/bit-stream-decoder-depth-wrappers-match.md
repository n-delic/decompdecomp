---
title: bit stream decoder matched with index staging, goto loops and depth wrappers
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2 -fno-schedule-insns
evidence: verified
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:

A 681-instruction N64 bit-stream decompressor with no calls (Huffman table builder in nine passes, then the decode
loop), KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -fno-schedule-insns`. It had been stuck at 70 for three sessions
(the earlier entries `bit-stream-decoder-*` record them). Every remaining row was a register choice or an order, in
the last table pass (a fill loop that writes each code's entries into a subtable) and in the two passes before it.

The source change, and the measured result before and after (scores, the judge used):

All scores are the app's (`fast_judge`, confirmed by `compile_and_diff`; the last one matched there).

1. 70 -> 105 -> 60: the fill loop's store, `(tab + ((ent >> 7) + t5))[y] = (vx << 7) + vy;`, written with the index
   staged first and the sum in integers:
   `u32 o1, o2; o2 = y * 2; vx = D_A[k]; o1 = ((ent >> 7) + t5) * 2; vy = D_B[k];`
   `*(u16 *)(o2 + (u32)((char *)tab + o1)) = (vx << 7) + vy;`
   Two rules decide it. sched2 keeps the RTL order of equal-priority insns, so the `sll` of the index computed first
   stays ahead of the two loads, which puts it first in the loop and lets reorg fill the back branch's slot from the
   target (`beql` with the `sll`, as the target has). And `ptr + int` is rebuilt pointer-first by the front end while
   `(plus (mult x 2) reg)` is RTL's canonical order, so `tab + x` always gave `addu v0, s7`-style `index + tab`; an
   integer sum with the scaled index as its own temporary keeps `tab` (and then `o2`) first, the target's operand
   order.
2. 60 -> 35: the natural `for (i = 1; i <= H[c]; k++)` form of the fill loop (u32 `ent`) with that store and the
   subtable shift count `((ent & 0x7FFF) >> 4) & 7`.
3. 35 -> 65 (registers right, then two pairs wrong): the pass that builds the mask uses `(1 << tb) - 1` in the target
   (the table's bit count, `tb`), which adds 3 loop-weighted references to `tb` and lets it outrank the fill loop's
   `H[c]` pointer (0.854 against 0.818). Writing the clearing loop and the short-code pass's middle loop as goto loops
   (`l5: ...; if ((i >> tb) == 0) goto l5;`) removed those loops' depth from `tb`'s references (56 -> 53) with no
   change in code: loop.c has nothing to do in either.
4. 65 -> 20 -> 0: the leftover pairs were each a loop-made pointer copy outranking a counter. `do { } while (0)` is a
   loop depth for flow.c and costs nothing, so wrapping a loop body's closing updates weighs them one more per depth:
   `do { do { do { off += 4; } while (0); } while (0); } while (0);` in the fill loop's middle loop;
   `i++; do { k++; t6 = *lim; } while (0); } while (t6 >= i);` in the short-code pass (the loop-made pointers are
   incremented right after `k`, so they gained the weight too; the exit test's load is inside the scope because a
   scope's loop notes are a sched2 barrier, and with `k++` alone one pointer's `addiu` moved before the load); and
   `do { off += 4; c++; q3++; } while (0);` at the end of its middle loop. That is scaffolding, not the original
   text. Fuzzed equal to the earlier 70-point source over 2000 random streams.

The replay in `alloc_roles.py` (`--whatif`) named every one of these: each step was "this pseudo x0.85" or "x1.15", and
`sweep`-style what-ifs over lives and references showed which ones a source change could reach.

Failed experiments that would otherwise be repeated:

- `register T *p asm("$16")` to pin the copy: the judge rejects inline assembly.
- An explicit pointer for the inner loop's `D_A[k]` (`u32 *pv = &D_A[k]`) anywhere in the k-loop: loop.c makes it the
  giv itself and the target's `move $s0, $t7` copy disappears (1704).
- The middle loop as a goto loop with user pointers: also removes that copy (1517).
- Pads that combine deletes only lengthen everything live across them; for these pairs (a copy against a counter of
  the same loop) no pad count works, because the competitor lives across the same insns. `vy % 0x10000` in the store
  is NOT deleted (it became `lhu`+`andi`); `((vx << 7) + vy) % 0x10000` is.
- A split `tb9 = tb` for the last pass: the copy survives (cse does not see it across the loop labels) and costs a move.

What is controlled evidence and what is one function:

Verified on one function. The two scheduling/operand-order rules are general GCC 2.7 behaviour (sched.c's luid tie
break, c-typeck's pointer-first sums and RTL canonical order). The depth-wrapper lever is decomp-repair's
`update_run_scope` family (decomp-repair #1659): from the 20-point candidate its children reach 0; the `% 2^n`
respelling is in `narrow_shift_mask` (same pull request).
