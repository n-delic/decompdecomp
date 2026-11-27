---
title: returned cursor record typed draft
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: one function
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 76-instruction N64 display-list setup
routine that takes the cursor, writes six commands and returns the advanced cursor, with the compiler and flags above.
The saved best was an m2c draft whose accesses had been typed as records (`((Rec_f_p *) p)->mN`), scoring 3342.

The source change, and the measured result before and after (scores, the judge used): `returned_gfx_cursor` (libultra's
`gfx++` macro blocks) only read `M2C_FIELD` stores, so it offered nothing for the record spelling. Reading the record
accesses back as the same stores (decomp-repair pull request 2207) gives 3342 -> 205. `float_literal_global` then gives
0: the one differing load was a float word that the target loads through `$at` and our global read through a register of
its own. In the app's judge the submitted source was `*(f32 *) 0xADDRESS` at that word (0, recorded as matched); the
float literal child also scored 0 in the isolated judge.

Failed experiments that would otherwise be repeated: a local copy of the global (215), `extern const f32` (205), an
array or one-member struct extern (730).

What is controlled evidence and what is one function: the family has a unit test; the scores are one function. That the
float is a literal-pool word loaded by the assembler's macro is a hypothesis read off the target.
