---
title: Indexed pointer record from a byte stride
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler loads a table base, scales an index by 16 and
reads the first word of the selected entry as a pointer. The draft expressed
this as a byte address followed by a pointer dereference. Giving the table a
16-byte record type with the pointer at offset zero changed an isolated
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2` score from 235 to 225; the
app recorded 225.

The `target_pointer_record_index` repair produces an equivalent candidate
when the target has the base load, shift, add and pointer load and the flags
specify 32-bit pointers. Its padding records the stride rather than claiming
other field identities. Typing both related addresses still scored 225.
These are observations on one function, not controlled exact-match tests.
