---
title: m2c writes SN GCC's signed division with the sign subtracted twice
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

SN GCC 2.8.1 schedules the `sra x, 31` of a signed `x / N` (N not a power of two) before the `mfhi`. m2c then reads
the high word as `x / N` and keeps the sign subtraction too, so the draft has `x - ((q - (x >> 0x1F)) * N)` where the
game wrote `x % N`. It also passes the quotient and product, still in `$a0`/`$a1`, to the next call, whose callee takes
no arguments. A draft with an extra `subu` beside an `mfhi` is this.

On a 61-instruction object initialiser (saved best 105), with the app's isolated judge: the division alone 240, the
leftover arguments alone 170, both together 0. Two stores between the `mult` and the `mfhi` needed no source change
once both were right; a `do { } while (0)` left by a search around the remainder store had to be absent when the unread
quotient assignments were removed (240 with it, 0 without).

decomp-repair `doubled_division_sign` (0.6.268) offers the three children from the saved source; the second one was
exact there. One function; the reading of m2c's mistake is confirmed by the exact bytes, not by m2c's source.
