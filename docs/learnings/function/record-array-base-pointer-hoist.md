---
title: record array base pointer hoist
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 149-instruction initialiser: scalar stores, two loops over arrays, a nested loop that fills a row of `u16 [][4]` and,
under a flag, the five byte fields of a record in a table of 5-byte records. gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`
(the project default is `-O2`, under which the plain source scores 6361; `-O1 -fforce-addr` reached 480 and was not the
profile).

The target hoists the record table's address out of the outer loop (`lui $t0; addiu $t0` before it) and addresses the
fields off one register. Plain `table[i].a = 4;` loads each field through `$at` (1930). `Rec *r = &table[i];` gets the
register but leaves the address in the loop (465): the `.loop` dump says `move-insn savings 1 not desirable` for a symbol
load that lives only to the next add. `Rec *t = table; Rec *r = &t[i];` gives a longer-lived insn that loop.c moves: 0.
decomp-repair `record_slot_base_pointer` (after `record_slot_store_pointer`, as one child from plain member writes).

Failed experiments: the same address as `table + i`, `(Rec *)((s8 *)table + i * 5)` and a 2D `s8 table[][5]` all score
465; `u8 *p = D; p[0] = 0; p[1] = 0;` for two scalar byte stores made the score worse (cse folds it, and the direct
`D[0] = 0; D[1] = 0;` was already right).

Controlled: the prove_chain run from the plain source on this one function. That the address insn's lifetime decides
the hoist is read from the loop dump of this function, not from loop.c.
