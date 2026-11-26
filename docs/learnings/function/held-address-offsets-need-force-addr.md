---
title: held address offsets need force addr
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -fforce-addr
evidence: observation
date: 2026-11-26
---

A 96-instruction display-list routine filling the screen (libultra command writes, one rectangle read from a record
at a fixed offset in a symbol). Compiler gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32`. The target builds the record's
address into `$a2` and reads `0($a2)`, `2($a2)`, `4($a2)`, `6($a2)`.

Plain C with a record pointer set after the command cursor: 5185 under `-O2`, 1040 under `-O1` (the offsets come out
as absolute `%lo(sym + 2)`), 0 under `-O1 -fforce-addr` (judged by the app). With the pointer set before the cursor it
was 120 under `-O1 -fforce-addr`.

Failed: `-O1 -fforce-mem` (1040), `-O2 -fforce-addr` (5185), `-O2 -fno-schedule-insns -fno-schedule-insns2` (1040).

The profile probe now judges `-fforce-addr` for targets with this tell (`reads_through_held_address`). One function:
how many others were built so is not measured.
