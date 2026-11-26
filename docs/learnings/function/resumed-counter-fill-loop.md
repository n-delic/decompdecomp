---
title: A second loop resumes the first counter, masked again at its head
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

What the function looked like: a 61-instruction N64 table fill, two loops over 5-byte records, the second carrying on
from the first one's `u8` counter to a fixed end. The target keeps one register for the counter across both loops and
copies the masked value at the second loop's head (`andi $v0, $a2, 0xff; move $v1, $v0`).

The source change, and the measured result (the app's judge): from 180, `k = i & 0xFF; while (k < 200) { ...; k = (k + 1)
& 0xFF; }` written `while ((i & 0xFF) < 200) { k = i & 0xFF; ...; i++; }`, with `u8 i` and `base + (k * 4) + k` as
`base + k * 5` (all three together: 5), then `(u8 *) arg1 + off` as `(u8 *) (off + arg1)` (0). Families:
`counter_resume_loop`, `integer_pointer_sum`.

Failed experiments: the loop rewrite alone scores 940, with `u8 i` 540; a struct-array spelling of both loops 4095.

One function, exact; the rewrite keeps the values (neither counter nor copy is read after the loop).
