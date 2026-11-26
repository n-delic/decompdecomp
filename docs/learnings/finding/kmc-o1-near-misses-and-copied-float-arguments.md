---
title: Near-misses of an -O2 game that were -O1 code; equal float arguments are copied
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

An N64 game whose project default is KMC GCC 2.7.2 `-O2` had fourteen near-misses (5 to 2900 off under their saved
profile) that other agents had spent many attempts on. All fourteen were byte-identical under
`-G0 -mips3 -mgp32 -mfp32 -O1` (the app's judge, 2026-11-26), most from plain C written from the assembly. The saved C
had been tuned for `-O2` with fences and phantom locals, which hid it. A display-list writer's m2c temporaries needed no
change at all under `-O1` (0 at once); two others were exact through `default_into_else` and `parameter_width`.

Source facts seen under `-O1`:

- Two equal arguments set as `li $a1,K; move $a2,$a1` (zero included) are `f32` parameters passed `0.0f, 0.0f` or
  `0.5f, 0.5f`. Integer constants are set separately. (5 -> 0 and 200 -> 0; `float_argument_prototype`.)
- A parameter copied after the stack parameters' loads was declared narrow, as was the position it is added to
  (65 -> 0; `late_param_narrow`).
- `ori $r,$zero,0xFFFF` for a stored -1 is an unsigned halfword (200 -> 0; `unsigned_minus_one`).
- `v = K; if (!m) v = f(); G = v;` against `G = m ? K : f();` (110 -> 0).
- Display-list commands each through their own `Gfx *g = cursor++;` get the target's cursor register; one `g` reused for
  every command does not (240 -> 0).

Failed experiments that would otherwise be repeated: under the `-O2` default none of the clean forms came below 390.

Controlled evidence: each row above was measured by changing only that spelling on one function. That `-O1` covers this
stretch of the game is an observation from these functions. The reasons given for the copied constant and the late
parameter move are hypotheses; the compiler source was not read.

The app change: the profile probe now plans the project's most used per-function profiles (five functions or more).
