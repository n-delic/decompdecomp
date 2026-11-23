---
title: sound entity starter narrow copy and counter forms
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like and the exact compiler and flags:

A 384-instruction sound-entity starter (find a free slot of eight, fill two 0x80-byte index tables, count 0x40 bytes,
read an options record, a 64-iteration table loop, a 16-voice loop, hand out a wrapped id from a global with a retry loop).
KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`. The saved best was 2696 with 101 register rows: the candidate saved nine
registers (`$fp` too, frame 0x58 against 0x50) and kept `arg2` and `arg3` in the registers the target used for other values.

The source change, and the measured result before and after (isolated judge, then the app: 0, matched):

| step (saved best -> match) | score | what it changes |
| --- | --- | --- |
| voice loop cursor `p += 8` as `(arg2 + i * 8)`, counter step last (`walk_counter_for`) | 2696 -> 2387 | loop.c reduced the cursor beside the counter into a giv with a register of its own (`$fp`); a counted loop leaves one giv in `arg2`'s register |
| every counter of a width one variable (`loop_counter_unify`) | with the above, 2805 | `arg3` takes `$s0` and the counters `$s1` (one pseudo with every loop's references outranks the parameters in global.c) |
| the field just stored read back through a volatile lvalue (`store_reload_volatile`) | 2805 -> 2525 | cse forwards the store otherwise; the target reloads it |
| a byte read in both arms of an `if` read through a `u8` copy in the first arm (`arm_byte_copy_split`) | 2525 -> 2310 | cse leaves a copy of another machine mode alone; combine turns it into a `move`; the two arms stop sharing one address sum through the branch's delay slot |
| the slot number copied before the call-free retry loop (`post_loop_copy`) | 830 -> 505 | `move $a1, $s5` before the loop, read after it, moves the loop's pseudos one register |
| the first call argument of the 16-iteration loop as `i & 0xFF` at the call (`loop_argument_inline`) | 505 -> 305 | the entry copy is the same `andi` the loop repeats, not a folded constant |
| the byte counters of the four fill/index loops one `u8` variable (`loop_counter_unify`) | 255 -> 185 | constant and counter of each fill loop trade `$v1`/`$a0` |
| `neg = -1;` above the countdown loop's initialisations, and the last two initialisations swapped (`loop_literal_local`) | 185 -> 165 | the hoisted literal then comes first, as in the target |
| the store address staged in a local first, and the `\|` chain associated to the left (`store_address_stage`) | 165 -> 115 | each alone is 375 / 175; together the address chain and the value chain take the target's registers |
| `t = G; G = (t + 1) & M; id = t;` (`global_read_store_copy`) | 110 -> 0 | with the copy after the store cse cannot fold the read into the local |

The families above are in decomp-repair (arm_byte_copy_split, walk_counter_for, loop_counter_unify, post_loop_copy,
loop_argument_inline, loop_literal_local, store_address_stage, global_read_store_copy, store_reload_volatile); `reach.py`-style checks showed
that no earlier family offered a child that moved the score at any of those steps except where the table says so.

Failed experiments that would otherwise be repeated:

- Reading why the cursor loop kept `$fp`: five dest-address givs of one biv (`4..8($s3)`) are combined and reduced whenever
  two of them exist (`lifetime * threshold * benefit > insn_count`, loop.c); no spelling of the cursor itself avoids it, and
  the biv cannot be eliminated because its first use is above a label. The index form is the one that leaves the giv in
  the parameter's register.
- Priorities explain the register order but did not find the source: moving declarations, `register`, wrapping statements
  in `do { } while (0)` (it adds loop-depth weight to every name in the statement, so it also lifts the counter it was
  meant to out-rank) and a scratch union around a pointer and an integer were each tried alone at most one row.
- A volatile read of the entity field just stored (`*(void * volatile *) (e + 4)`) reproduces the target's reload of a field
  it stored one instruction earlier (cse forwards the store otherwise); no other spelling was found (a different cast,
  the member form, the cast form, a `u32` store), so the match carries it and `store_reload_volatile` offers it.

What is controlled evidence and what is one function:

Each row's score is from the isolated judge on this one function (observation at that step; the final result is verified by the
app: matched, 0). The row about a copy of another machine mode follows `narrow_copy_web`'s evidence and was measured here
once. Scaffolding the final source relies on: the volatile reload above, a stack scratch array and a union around the pointer
and value of the table loops (the frame is 0x50 with them), a few `do { } while (0)` weight scopes (a wrapper ladder on two
statements), a literal held in a local, and `neg = -1;`.
