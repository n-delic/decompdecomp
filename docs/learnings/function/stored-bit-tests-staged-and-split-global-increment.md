---
title: Stored single-bit tests staged in a local, and a global post-increment store split
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 511-instruction N64 input sampler,
SN64 GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. It unpacks 14 buttons of four pads
into 56 `s32` flags (34 of them spilled), then records or replays them.

The source change, and the measured result before and after (scores, the judge used): the app's judge. A typed
rewrite from the assembly with 56 separate `s32` flags scored 5920 (saved best 15547, 25 attempts).
`flag = (x & 0x200) != 0;` compiles to `srl; andi 1`; staged as `t = x & 0x200; flag = t != 0;` it is the target's
`andi; sltu`: 1210 with one shared `t`, 750 with one local per test. `buffer[index++] = word;` -> `buffer[index] =
word; index++;` on a global `u16` index then gave 0 (the target reloads the index after computing the word).
Families: `assigned_bit_test_stage`, `global_post_increment_split`. From the m2c draft,
`stack_word_half_read` then separate locals for the `sp10[34]` spill array (`spill_array_scalars`) scored 15355
(24715 with the reads repaired alone), and the bit staging on that 14670.

Failed experiments that would otherwise be repeated: `held(x, mask)` as an inline function gave a byte load and
`andi 1` in a snippet; `x ? 1 : 0`, `!!x` and `(u32) x > 0` all keep the shifted single-bit form.

What is controlled evidence and what is one function: the scores are controlled runs on one function. Why the
spellings differ (`do_store_flag`'s single-bit path, the index reload) is a hypothesis, not read in the compiler
source.
