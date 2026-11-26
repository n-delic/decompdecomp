---
title: A byte-record copy loop under -O1 with symbol-indexed reads and hoisted store bases
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 71-instruction initialiser copies four bytes between two tables, zeroes sixteen, then copies 200 five-byte
records. The target keeps both table addresses of the four-byte copy in registers, reads each record byte as
`lbu sym($i5)` and stores through five hoisted bases.

Exact (app judge, per-function `-O1`): the four bytes written directly, `DST[0] = SRC[0]; ...` (no pointer locals),
and the loop as `D[i * 5 + k] = RECORDS[i].field;` with a five-byte source record. The destination as five separate
byte symbols works too.

Measured on the loop (same function): destination and source both as separate symbols indexed `[i * 5]` 2670; a
record destination 2700 to 3385. From the 2670 form `record_slot_rows` alone reached 0 in five greedy steps under
`-O1`. With pointer locals for the four-byte copy, `local_weight_ladder` reached 0 in one step (2120 -> 0).
Under the project's `-O2` default the exact source scores 5570, so earlier attempts on near-misses under it could
not close. `memcpy` and a struct assignment give `lwl`/`lwr`.

One function; not controlled beyond it.
