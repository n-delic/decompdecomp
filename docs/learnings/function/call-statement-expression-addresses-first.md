---
title: element addresses computed before a call need the call in a statement expression
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 150-instruction N64 function ends with a loop storing one sine-scaled halfword into two rows of a vertex array. The
target computes both element addresses (`sll`, `addu` into saved registers) before the `jal`; every plain spelling
computed them after it (GCC 2.7 `preexpand_calls` expands the call first).

The source change: `a[i] = a[i + 8] = __sinf(x) * s[i];` as `... = ({ __sinf(x); }) * s[i];`. 1817 to 0 under -O1
(-O2 left 3286-5971 whatever the spelling; the function is -O1). The rodata doubles had to be read (`read_data`):
the step is 4.0 and the constant 3.14, not the draft's 1.0 and 3.14159; a wrong literal shows as score 1 with no
differing rows.

Failed: casts, `(0, call)`, `1 ? call : 0`, a value temporary, a pointer local for the element (best 525).

One function (controlled: only the wrapper changed). Library family: `call_statement_expression`.
