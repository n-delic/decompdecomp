---
title: a call argument chosen by a ladder of bit tests takes the argument register unless its copy is a subreg copy (u8 result, else-if chain, s32 return)
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

An 89-instruction audio-setup function (calls, then four tests of bits 16-19 of its fourth parameter choosing a small
number that is the last argument of a final call, returning 0) sat at 90 in the app's judge. Six register rows and two
ordering rows: the target holds the chosen number in `$v0` (every `addiu $v0,$zero,N` in a branch delay slot, the last
test's `sltiu $v0,$v0,1`, then `move $a3,$v0` in the delay slot of the call), the candidate held it in `$a3`.

| edit | score |
| --- | --- |
| saved best (`unsigned long long` return, `s32 var`, nest of defaults with a `do { } while (0)`) | 90 |
| return type `s32` alone | 180 |
| `u8 var` alone (return `unsigned long long`) | 365 |
| else-if chain alone | 90 |
| else-if chain + `u8 var`, return `unsigned long long` (one row: `move $v1,$zero` the target lacks) | 100 |
| else-if chain + `s32 var` + `s32` return | 180 |
| else-if chain + `u8 var` + `s32` return | 0 |

No scaffolding: the chain, a byte for a value from 0 to 4 and a 32-bit return are what a developer writes.

Why (read from the greg dump and global.c, not confirmed stage by stage): the result pseudo is live while `$a0-$a2` are
set but never meets `$a3` or `$v0`. `find_reg` takes the lowest free register, then lets a *copy* preference override it:
`set_preference` records a plain `(set (reg a3) (reg var))` as a copy preference (`copy = 1`), a source with a `subreg` or
an extend as a plain preference only, and the copy ones are tried first. With the result as `u8` its move into `$a3` is
a subreg copy, the last test's `sltiu` result adds a plain preference for `$v0`, and the lowest preferred register
(`$v0`) wins. The else-if form is needed so the constants are assigned after their tests (the nest's early default made
the result live across the test's `$v0` temporary). The 64-bit return only adds the `move $v1,$zero` the target lacks.
`passes.py` now prints each pseudo's hard-register conflicts, which showed the free `$a3`/`$v0`.

decomprepair `ladder_result_narrow` (0.5.655) offers the three as one child first, then the chain with the byte, the chain
alone, the return type alone; its first child from the saved best is the exact source. Controlled by the table above on
one function; the allocator explanation is a hypothesis the judge was not asked to confirm separately.
