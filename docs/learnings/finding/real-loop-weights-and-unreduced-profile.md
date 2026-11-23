---
title: a table loop's register order wants the real do/while and the unreduced profile together
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 (-fno-strength-reduce)
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 132-instruction N64 voice-table search (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`): a loop over 0x1a0-byte records
with four induction values (counter, record pointer, pointer + 0x24, byte offset), nine saved registers and a table
store through `lui $at; addu $at,$at,idx; sw %lo(sym)($at)`. The saved best (325) was m2c's `L: { ... } if (i < (u8) n)
goto L;` carrying `do { } while (0)` weight ladders, and every attempt of earlier agents (about 300) left the saved
registers rotated: counter/last-index, offset/record-address and pointer/offset pairs swapped, plus a `$v1`/`$a0` swap for
a flag word and its mask.

The source change, and the measured result before and after (scores, the judge used):

| edit | score |
| --- | --- |
| saved best | 325 |
| entry guard `if (n != 0)` as `if (i < n)`, the counter's compare (the missing 8-byte spill slot; `n` is a global byte) | 310 |
| the ladders removed, the loop written `do { } while (c)`, profile `-fno-strength-reduce` (the `lui $at; addu $at` idiom) | 420 |
| the symbol-indexed store written `q = (T *)(SYM + idx); *q = v;` | 95 (every register but one pair right) |
| `var_s5 = ...` set wrapped twice in `do { } while (0)` | 0 |
| (before those: `flags \|= 0x80800` after the quotient 295 -> 255; halfword reload and byte load first 255 -> 135, under the goto loop) | |

Rules found (each measured, the explanations are hypotheses read from flow.c, global.c and loop.c):

- A backward `goto` loop has no loop notes, so every reference weighs 1; `do { } while (c)` weighs references inside by the
  loop's depth (`reg_n_refs += loop_depth`), which reorders global.c's priorities. The goto form needed a rack of ladders
  to fake what the real loop gives, and still left a pointer/counter pair the ladders could not move.
- Under the real loop loop.c hoists a separate `la SYM` out of the loop, and cse2 then cannot fold it into the store's
  `lui $at/addu` address (hoisted: `lui t1; addiu t1; addu v1,v1,t1; sw 0(v1)`). A pointer local for the address keeps the
  `la` where it was and the fold happens (420 -> 95).
- The real loop under the old profile scored 4924 and the profile under the goto loop did not change the score: the two
  only pay off together.

Failed experiments that would otherwise be repeated:

Random and exhaustive ladder searches (about 100k children) under the goto loop stopped at 18-20 differing rows; a
struct-typed rewrite of the table (natural code, loop.c reduces the row to one pointer) has fewer live values than the
target's four and spills nothing; `idx * 0x1A0` spelled in the call's address is expanded to shifts, so loop.c has no mult
to reduce.

What is controlled evidence and what is one function:

One function. decomp-repair `real_loop_unreduced` (loop, store pointer and the joint profile probe) and
`statement_slide` (the two order slides) offer the steps; `loop_guard_counter_compare` takes a global count and the goto
closing form (decomp-repair PR 1334).
