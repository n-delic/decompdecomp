---
title: three byte updates in a row, one value read early and the next read inside a one-shot scope
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 39-instruction N64 object timer ending in three bytes of one record each lowered by 50. The target uses two registers:
`lbu v0,X; lbu v1,Y; addiu v0; sb v0,X; lbu v0,Z; addiu v1; sb v1,Y; addiu v0; sb v0,Z`. Three compound updates give
three registers (the first scheduler reads all three bytes first; byte offsets from one base cannot alias).

The source change, and the measured result before and after (scores, the judge used):

    y = rec[Y];
    rec[X] -= 0x32;
    do { z = rec[Z]; rec[Y] = y - 0x32; } while (0);
    rec[Z] = z - 0x32;

Saved best 60 -> 0 (the app's judge, recorded as matched; per-function profile KMC 2.7.2 above). Family:
decomp-repair `rmw_run_load_fence`, which offers it from the saved best, its scaffolding-stripped form and plain
compound updates. The loop-note rule (the insn after a loop note depends on all before it) is read from `sched.c`.

Failed experiments that would otherwise be repeated:

- All six orders of three compound updates: 330 to 375. Struct, array, `s8` and `x = x - K` spellings of them compile
  alike.
- Orders with the third byte held in a temporary, with and without one scope around single statements: 30 at best.
- Marking one or two of the accesses `volatile`, over every order: 140 at best.
- The `sn-n64-281` profile on the exact source: 270.
- `prove_chain --families all` on the saved best: none of 624 children improved on 60.

What is controlled evidence and what is one function:

One function. The register outcome has not been tried elsewhere.
