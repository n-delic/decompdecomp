---
title: stack slot record copy dot append
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 39-instruction N64 routine that appends
one 16-byte record (three truncated floats, a fourth word left unset) to a table and bumps its counter. The target ends
with four `lw` then four `sw` through the table slot: a record assignment. m2c wrote `u8 sp[16]`, a `#define` per slot
and four word stores; the saved best added a pointer to the counter and `do { } while (0)` fences.

The source change, and the measured result before and after (scores, the judge used): the app's judge, from the saved
best (860): the slots as one record copied whole, 295; every fence opened, 120; the counter bump moved below the third
member's write, 0. The clean spelling (`Dot dot; ... gCount = n + 1; table[n] = dot;`) was also exact at once.
decomp-repair `stack_slot_record_copy_bump_late` reaches 0 from the saved best in one step.

Failed experiments that would otherwise be repeated: with the record copy, `weight_scope_unwrap` reaches 60 and then no
family improves; moving the bump while its fences are only partly opened scored 625.

What is controlled evidence and what is one function: one function.
