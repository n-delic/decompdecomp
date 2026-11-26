---
title: At -O1, a table base copied from the entry test's address, and neighbouring globals reached from one register, come from the source's shape
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Three N64 functions whose delay slots are mostly unfilled: matched by none of the -O2 profiles tried, exact at
`-G0 -mips3 -mgp32 -mfp32 -O1` set for each function alone.

The source change, and the measured result before and after (scores, the judge used):

- A list walk whose target forms a table's address for the entry test and then copies it (`lui; addiu; move $v1,$v0`)
  into the register the body reads: the pointer local is set as the first statement of the loop body and the loop's
  condition reads the array itself (115 -> 0; families `loop_head_base_set`). `f(x, ids[i++])` through a function
  pointer copied the old counter first: `i++;` after the call (420 -> 115; `call_arg_postinc_statement`).
- A float state update: the product stored to one field is computed in place in the local that later holds the blend
  rate (65 -> 0; the library already reached it from the m2c draft once its float table was typed `f32`:
  `float_word_load_retype`, then `store_tail_into_arms`).
- A clamp routine that stores two globals through a third's address register (`-0x12($s0)`): the globals are members
  of one record (4406 -> 0; `neighbour_globals_record`), and the clamp compares `cur > max` (`comparison_sides`).

Failed experiments that would otherwise be repeated:

`p = TABLE;` before the loop and again at its end, or `(p = TABLE)[i]` in the condition, all coalesce the copy (420).
The clamp routine as separate externs under `gcc-2.8.1-pm-sn-as -O2` stopped at 170 across about two dozen earlier tries.

What is controlled evidence and what is one function:

Each is one function; the scores are the app's judge.
