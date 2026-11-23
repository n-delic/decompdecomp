---
title: input handler m2c chain 9943 to 0
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 416-instruction N64 input handler (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`). A loop over four players filters two
stick axes (dead zone, a divide by 76 as a multiply, a clamp to +-0x10000), stores the results and their deltas in
0xA0-byte records and calls two helpers per axis; a second loop over a variable player count runs a 3-button chord
state machine (a jump table of seven states plus a default, a continue flag, a 36-byte flag block cleared with memset),
then calls one helper twice per player through a second jump table of seven arms. The saved best (9943 in the isolated
judge, 5773 as the app recorded it) was m2c's output after many agents' edits: byte-offset cursors written as casts of
absolute addresses, a `volatile long long` return type for a one-word callee, a `volatile u64` frame pad, an unused
`long long` local, `;;;;` statements, an uninitialised temporary in one arm, a `do { break; } while (0);` that ran one
arm into the next.

The source change, and the measured result before and after (scores, the judge used):

Isolated judge (`fast_judge.py`, the app's own `judge-worker`), 9943 -> 0, recorded 0 by the app. In order, with the score
after each step (each step also reproduced by the library family named, which takes the previous step's source):

- the first loop as a counted loop over typed records (`Src8 { s16 a, b, c, d }` rows of 8, `Dst160` rows of 0xA0), one
  variable for the filtered value, the delta stored before the value: 9943 -> 4846. No family: the saved best's casts of
  absolute addresses cannot be read back into rows (a remaining permuter gap).
- the callee `volatile long long f(s32)` as `s32` (it returns one word; the wide result was copied out of `$v1`):
  4846 -> 4786 (`wide_return_prototype`).
- the loop count as a plain `s16` local instead of a volatile or a frame pad: 4786 -> 4236 (spilled by reload, so the
  frame matched without a pad).
- `i * 8` of the second loop as a local at the top of the body: 6575 -> 5445 (`loop_scaled_index_local`).
- the cleared block's scalars and `(char *)&D_X - 0x10` casts as `D_80112E98[k]`: 4236 -> 3145 (`symbol_run_array`); the
  hoisted address registers `t3..t7` are then the target's and every temporary after them moves into place.
- the call every arm ends with, in each arm, with the byte parameter `u8`: 3145 -> 905 (`switch_arm_tail_calls`).
- `v = ((v & m) << 16) / 76;` as `v &= m; v = (v << 16) / 76;`: 905 -> 885 (`self_mask_split`).
- the state machine without its dead tests, `goto`s and fallthrough, the exit arm first, the join statement once:
  885 -> 20 together with the loop below (`switch_dead_exit_strip`, `goto_arm_tail_inline`, `exit_arm_first`,
  `common_arm_tail_hoist`).
- the guarded do-while counted loop as the `for` (frame 8 bytes larger, every stack row matched): `guarded_do_to_for`.
- `n = 4; if (flag) n = u16 global;` as `if (!flag) n = 4; else n = u16 global;`: 20 -> 0 (`const_arm_conditional_set`).

From the saved source with the first loop already typed, the library chain `switch_dead_exit_strip`, `goto_arm_tail_inline`,
`exit_arm_first`, `guarded_do_to_for`, `const_arm_conditional_set`, `common_arm_tail_hoist` goes 885 -> 0.

Failed experiments that would otherwise be repeated:

- Typing the loop count with a separate temporary (`x = flag ? a : 4; n = x;`, every type of x and n, `register`, an `&`
  mask): all 980 or worse. cse copy-propagates `x` into the loop's entry test, so the test never reloads the spilled
  `n` as the target does.
- `n = flag ? a : 4;`, `if (flag) n = a; else n = 4;` and `n = 4; if (flag) n = a;` all compile to the same two stores:
  jump.c's `Simplify if (...) x = a; else x = b;` rule turns them into `x = b; if (...) x = a;` whenever `b` is a constant
  or register. The constant as the then arm of the negated test is the one spelling it does not rewrite.
- Mask temporaries written inline (`(btn & 0x2000)` for `temp_a2`) instead of locals: 885 -> 2714.
- A volatile loop count (`volatile s16 n`): the slot is a real local at the wrong offset (2004 and 2919).
- An unused `long long` local or a `volatile u64` pad for the frame: not needed once the loop is a `for`.
- With several spellings of the continue flag (`u8`, `s16`, `register`, `while (var)`): the register of the flag did not
  move; the dead tests were the cause (references of the masks: 20, of the flag: 14; priority is
  `floor_log2(refs) * refs / live`).

What is controlled evidence and what is one function:

One function. The effects of each rewrite were measured by applying the family's output to the intermediate source and
judging it (the numbers above); the chain at the end reaches 0 from the saved source with the first loop typed. The rules
that generalise: a `for` leaves stale pseudos that give the frame its 8-byte hole where the guarded do-while does not;
cse's related-address registers are created per symbol, so the words of one cleared block must be one symbol; a spilled
user variable whose two sets are a constant arm and a load arm stores once after the join only when jump.c's if/else
rule does not fire.
