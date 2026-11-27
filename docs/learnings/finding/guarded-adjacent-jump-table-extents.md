---
title: Guarded adjacent jump table extents
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A large N64 function has three consecutive switch tables, each guarded by `sltiu` against eight. All entries point
inside the same function. Scanning until a word is not a case target recovered 24, 16 and 8 entries, giving m2c
overlapping switches. Supplying the eight entries for each table produced a 32,117-byte draft; the unrestricted
recovery produced a different 34,863-byte draft. The app had also recorded an ambiguous-table drafting failure.

The dispatch sequence proves the bound when it tests the index, branches out of range, scales that same index, and
loads and jumps through the named table. Bound inferred extents by that count; keep the old scan for other shapes.
A controlled synthetic regression checks two adjacent tables and rejects changed indices, branches and jump registers.
This establishes draft input recovery, not an exact function match. A manually corrected draft scored 100,785 with
the app's exact judge at the profile above; an app diagnostic trial at `-O1` scored 49,835. Neither is a controlled
measurement of the fix alone and neither is a match.

Simply counting words that point into the function does not distinguish adjacent tables that target the same body.
