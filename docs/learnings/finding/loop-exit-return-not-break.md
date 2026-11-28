---
title: A loop exit written as the following return, not a break
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

An N64 controller setup function (118 instructions): a clearing loop, then a loop over four ports that stops at a
caller's limit and returns the global count. It was filed under a KMC GCC 2.7.2 range; its matched neighbours were
built with the SN profile above.

Symptom: plain C (typed records, two indexed `for` loops) scored 210. The exit branch had `lui $v0, %hi(count)` in
its delay slot where the target has `nop`, and the target branches past the closing `lui; lw` of the count.

Change: `if (count == max) { break; }` to `if (count == max) { return count; }`, with `return count;` still after the
loop. 210 to 0 (the app's judge and a `compiler_experiments` trial). decomp-repair `break_as_return`
(`return_as_break` is the reverse) makes that child.

Failed: the scaffolded saved best from the KMC profile (2640 there) scored 3518 under the SN profile, and
`break_as_return` on it gave 3612. A 50-judge family chain from it reached 2837. The match came from a plain rewrite
under the neighbours' profile, not from edits to the old best.

One function's measurement. The explanation (the value is already in `$v0` on that exit) is read from the two
listings, not from the compiler's source.
