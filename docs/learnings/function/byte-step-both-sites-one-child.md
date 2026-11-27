---
title: a table slot indexed twice needs both m2c byte steps fixed together
kind: function
compiler: SN GCC 2.8.1 -O2 -mips3 -mfp64
evidence: verified
date: 2026-11-27
---

An 88-instruction N64 object update, built with `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. m2c
declared a pointer table as `extern M2C_UNK D;` and read one slot as `*((i * 4) + &D)` at two places; C scales that
index again (`sll` by 4 where the target has 2).

Writing both reads as `(&D)[i]` is the whole difference: the app's judge gave the machine draft 300, one site
rewritten 150 (the draft cleanup took `pointer_byte_step`'s first child only) and both 0 (verified, one function).
`pointer_byte_step_all` (decomp-repair 0.6.282) offers every site in one child, and the draft cleanup uses it.

Game-wide drafts with two or more sites, judged in isolation, one site against all: 14 of 15 better, 1 worse
(an observation about starting scores, not exact matches).
