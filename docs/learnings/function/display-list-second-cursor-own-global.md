---
title: Display-list tail through a second cursor global, and the address sum regrouped
kind: function
compiler: KMC GCC 2.7.2 -O1
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 73-instruction display-list builder (`-G0 -mips3 -mgp32 -mfp32 -O1`; saved under `-O2` at 940). The last command
goes through a second cursor stored in the middle of a larger block, which the target loads and stores with its own
`lui` each time.

The source change, and the measured result before and after (scores, the judge used):
Judged by the app. Under `-O1` the machine draft is 845; `symbol_address_local` 340, `m2c_refinement` 60, and writing
the first command's address `(b << 11) + ((a << 12) + table)` instead of `(a << 12) + ((b << 11) + table)`, 0.
`reassociate` had no child there: the statement was `M2C_FIELD(p, s32 **, 4) = ...` and the table a plain `char`
array (fixed, decomp-repair #1837). Also exact: the second cursor declared as its own pointer global at its address.

Failed experiments that would otherwise be repeated:
Every `-O2` spelling (5250 and up). The second cursor as a member or element of the block's symbol under `-O1`
(best 505) or as a cast offset from it (635 until the address was held in a local pointer).

What is controlled evidence and what is one function:
Controlled: the reassociate children (two of five score 0). One function: the profile and the second cursor.
