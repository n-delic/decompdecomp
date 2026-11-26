---
title: m2c's rotated loop cursor is the index expression; written as a second local it costs a move
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 97-instruction N64 search over seven
entries, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (its own profile was the -O2 default). The loop's first
instruction is `addu $v0,$s1,$s0`, repeated in the bottom branch's delay slot; m2c writes it as `var_v0 = arg1;`
above the loop and `var_v0 = arg1 + var_s0;` after the step.

The source change, and the measured result before and after (scores, the judge used): from the machine draft,
`prove_chain.py` under that profile reached 210 (rows: an extra `move`, the cursor in another register). Reading
`arg1 + var_s0` where the cursor was read and dropping both assignments scored 0 (decomp-repair
`rotated_cursor_inline`). The function itself was matched first from plain struct-array C under the same profile.

Failed experiments that would otherwise be repeated: earlier attempts under -O2 and with pointer cursors stayed
above 800.

What is controlled evidence and what is one function: the one-edit step from the chain's source is measured; one
function.
