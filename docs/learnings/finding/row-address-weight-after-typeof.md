---
title: row address weight after typeof
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 583-instruction N64 state handler computed a row address after several leading local declarations, including a `__typeof__` declaration. Wrapping the real row-address assignment in one one-shot scope reduced the isolated linked O2 diagnostic from 20,118 to 20,033. The scope is allocation scaffolding. The existing statement-weight repair missed the candidate because its declaration scan stopped at the earlier `__typeof__`; continuing that scan offers the exact child.

The app submission is pending. This is one function observation, not an exact match or a controlled regression test.
