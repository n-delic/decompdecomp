---
title: Read-first local record initialization
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler initializes a local configuration record:
two fields receive zero and a third reads a value from a channel table. The
target loads the read-derived value before the zero stores. Moving that
field initialization before the other two changed the isolated score from
2030 to 1910 under gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`. The
`local_record_read_first` repair emits that exact source only when the record
has not escaped and its three fields are distinct. The app judge request was
still running when this evidence was recorded.

The same ordering change on an earlier source scored 2745 to 2625. These are
observations on one function, not controlled exact-match tests; the compiler
scheduling explanation is a hypothesis.
