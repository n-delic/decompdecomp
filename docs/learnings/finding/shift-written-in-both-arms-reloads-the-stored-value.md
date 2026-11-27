---
title: A shift written in both arms is merged into one and the stored value is read back
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 265-instruction homing update (flags above). Each arm computes a value, one shared `sll 1` and `sw` follow the join, and the next call reloads the field instead of reusing the register.

The source change, and the measured result before and after (scores, the judge used): writing `field = (a - b) * 2;` in one arm and `field = -y * 2;` in the other (cross-jumping merges the shift and store): 150 -> 0, the app's judge. The m2c draft had `a - b * 2` in the first arm, which is a different program: check the shift at a join against both arms.

Failed experiments that would otherwise be repeated: a ternary times 2 stored once forwards the shifted register into the call argument; `d = ...; field = d * 2;` does the same.

What is controlled evidence and what is one function: one function.
