---
title: A goto loop with a test at its head is while (1) with the exit in the else (SN GCC 2.8.1)
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

A 31-instruction N64 loop that formats a numbered name and loads it. The target keeps `andi; sltu; beqz` at the head of
the loop and a `j` back with the counter step in its slot, and takes the format string as `lui` before the loop and
`addiu %lo` inside it. The project default (KMC GCC 2.7.2) saved best was 300; the function was built by SN GCC 2.8.1
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`, applied to this function only.

The source: `while (1) { if ((u16) i >= count) break; ...; i++; }` with the string symbol written at the call. The app
records 0. Scores under SN GCC 2.8.1 (app judge worker), same body each time:

- `for (i = 0; (u16) i < count; i++)`: 1445 (the loop is rotated: guard plus a test at the foot).
- `label: if (...) { ...; goto label; }`: 958.
- `while (1) { if (...) { ...} else break; }` with the symbol at the call: 0.
- The same with the address held in a local (`base = SYM;` before the loop): 500.
- m2c's `label: temp = i & 0xFFFF; if (temp < n) { ...; goto label; }` as `while (1) { temp = ...; if (temp < n) { ... }
  else break; }`: 500 with the base local, 0 with the symbol written out.

The permuter reached this only in three families in turn (`goto_structure_repairs`, `loop_else_break`,
`symbol_base_inline`), the first scoring worse. decomp-repair `loop_else_break` now takes the goto loop with statements
before its test, and `loop_else_break_base` also writes the base local out: exact in one step from the saved best with
its scaffolding stripped. With the scaffolding (identical-arm guard) left in, the loop child scored 842.

Failed experiments: under the default KMC profile the `while (1)` form scored 468 and the saved best stayed at 300, so
the profile and the loop shape have to be tried together. SN GCC 2.7.2 build 0006 gave 160 on the exact source.

One function: the profile and loop forms are measured here only; why the compiler leaves the test at the head is a
hypothesis (no source for SN's builds).
