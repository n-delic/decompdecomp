---
title: a rotated-offset distance trigger: float literals, trig calls into locals, the per-function -mfp32 profile (11184 -> 0)
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mfp32
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 173-instruction N64 object handler. It rotates an offset by the object's angle (`coss`/`sins` of a `u16` at
0x12), adds it to the object's position, and compares the distance to another object with `sqrtf` (the target calls
the NaN fallback `func_80094350` after `sqrt.s`/`c.eq.s`). The saved best was the m2c draft at 11184.

The source change, and the measured result before and after (scores, the judge used):

- A record type for the object, the m2c temporaries as plain C, and `v[3]`, `r[3]`, `p[3]` arrays for the three
  stack vectors: 11184 -> 2192.
- The six `lui $at; lwc1` pool words as float literals (10, 12, 10, 1400, 900, 600, read with `read_data`): 837.
- The second rotation calls `sins`, then `coss`, and multiplies the sine term first. Both calls go into `s16` locals
  before the products (61 under `-mfp32`). Then the difference is written
  `-((v[0] * sn) >> 15) + ((v[2] * cs) >> 15)`: 0. `prove_chain` finds that last step itself from the 61 state
  (`temporary_hoist_block`).
- The target never uses odd float registers. The `compiler_experiments` trial
  `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32` was exact and was applied with `suggest` as the
  function's own profile.

Failed experiments that would otherwise be repeated:

- Without `-mfp32` the same source scores 195.

What is controlled evidence and what is one function:

One function, judged by the app (the exact trial applied with `suggest`).
