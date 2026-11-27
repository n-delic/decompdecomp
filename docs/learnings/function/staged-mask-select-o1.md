---
title: staged mask select o1
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 201-instruction function: a guard on a byte header record, a `do`/`while` that draws a random bank, then eleven
flag-gated word ORs into a record array of eight words.

Exact in plain C under `-O1` (per-function profile): the header declared as a struct (so its address is built once and
both bytes are read from it), the record array as `Rec D[]` with `f[8]`, and the branch-free select
`sltu $s0; negu $s0; and $s0` written in steps, `i = r != 3; i = -i; i &= r;`. `?:`, `-(r != 3) & r` and
`(r != 3) * r` all scored 15 (the select computed in `$v1`).

The library already reaches 0 without the staged select: from the plain `do`/`while` candidate `loop_goto_form` alone,
and from the machine draft judged under `-O1` an 11-step chain (mostly `guard_temp_sink`). What blocked it was the
profile: under `-O2` nothing came close. Controlled for this one function.
