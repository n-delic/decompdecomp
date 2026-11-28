---
title: A symbol-plus-register load through $at with a free destination is the SN assembler's
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g vs sn-n64-272-0001-as0004
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 45-instruction function of an N64 KMC game (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g`) at 15 with three rows
left, all on one load from a byte table indexed by a parameter: the target has
`lui $at, %hi(T); addu $at, $at, $s0; lbu $v1, %lo(T)($at)`, ours `lui $v1; addu $v1, $v1, $s0; lbu $v1, %lo(T)($v1)`.
cc1 emits the same `lbu $3,T($16)` either way (the `.greg` dump has `(mem (plus (reg s0) (symbol_ref T)))`): the
difference is the assembler's expansion of the macro. GNU as (KMC's `as`, and gcc-2.8.1-pm's) uses the destination as
the temporary unless it is the base (checked by assembling `lbu $3,T($16)` with KMC's own `as`); SN's asn64 always uses
`$at`.

The source change, and the measured result before and after (scores, the judge used):

None. The saved C under `compiler_experiments` with `sn-n64-272-0001-as0004 -G0 -mips3 -O1 -g` was EXACT (app judge),
applied with `suggest`: 15 -> 0. The same game has this form in about 370 functions and the GNU form in 3; matched ones
with it sit on `gcc-2.8.1-pm-sn-as`, `sn-n64-272-0001` and the as0004 pack.

Failed experiments that would otherwise be repeated:

The pointer-dereference spelling of the load (15) and a signed cast (210) under KMC: no spelling can change an
assembler's macro expansion. On a 22-instruction KMC `-O2` function with the same tell (585), the SN packs were 700
and gcc-2.8.1-pm-sn-as 585 with and without `-mgp32 -mfp32`: the tell names the assembler, the source can still be off.

What is controlled evidence and what is one function:

The assembler behaviour is verified (both assemblers run on one line). The match is one function. The rule is the
profile probe's `sn_assembler` family (`arch/mips_profile_evidence.word_load_through_at`,
`consoles/n64/profile_probes.py`).
