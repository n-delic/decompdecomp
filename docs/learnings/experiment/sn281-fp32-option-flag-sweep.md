---
title: An option-flag sweep does not close SN 2.8.1 near-misses; -mfp32 is the flag that matters
kind: experiment
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32
evidence: controlled
date: 2026-11-26
---

Nine open functions of one N64 game (37 to 1585 instructions), each at its saved best C, built with their profile
(`... -mfp64`) and with `-mfp32` appended. Under `-mfp32` most of them line up with the target block for block, with
register and order rows left. The question: does any compiler option close those rows?

Measured (6,712 compiles): about 90 `-f`/`-m`/`-g`/`-O`/`-G` options singly, a dozen hand-picked pairs, then pairs and
triples of the options that changed the output, on `sn-n64-281` and `sn-n64-272-0001` (the latter without
`-mcpu=VR4300`, which it rejects), plus the other SN assembler and compiler pairings with a few flags.

| function | profile (-mfp64) | -mfp32 | best of the sweep |
|---|---|---|---|
| func_8001EBF4 | 200 | 200 | 200 (-mfp32) |
| func_8002AFFC | 205 | 105 | 105 (-mfp32) |
| func_80034E40 | 250 | 220 | 220 (-mfp32) |
| func_80082990 | 310 | 310 | 310 (-mfp32) |
| func_800202BC | 530 | 500 | 500 (-mfp32) |
| func_80021818 | 2617 | 2372 | 2372 (-mfp32); alpha local as s32: 2652 |
| func_80037164 | 2600 | 1310 | 1310 (-mfp32) |
| func_80047B34 | 7616 | 2756 | 2676 (-mfp32 -fforce-addr) |
| func_8000FAC4 | 11530 | 10 | 10 (-mfp32) |

Results:

- No option set reached 0. Apart from `-fforce-addr` on one function (80 points, probably not the real flag), nothing
  beat plain `-mfp32`. The remaining rows are source-shape rows: do not repeat the sweep on these.
- No effect on any of the nine (identical output): `-g`/`-g1`/`-g2`/`-g3`, `-O3`, `-fomit-frame-pointer`,
  `-fcaller-saves`, `-fno-defer-pop`, `-fno-peephole`, `-fno-thread-jumps`, `-funroll-loops`, `-funsigned-char`,
  `-mips2`, `-mips3`, `-msplit-addresses`, `-mgpopt`, `-mgpOPT`, `-mgp32`.
- Rejected by cc1n64 2.8.1 here (no object): `-fmove-all-movables`, `-freduce-all-givs`, `-fno-rerun-loop-opt`, `-Os`,
  `-mips4`, `-mcpu=r3000`.
- The SN assembler's own options are not part of a profile: `consoles/n64/sn64.py` runs `asn64` with fixed arguments,
  so they were only varied through the other SN assembler pairings (none beat `sn-n64-281`).
- `sn-n64-272-0001` was far worse on every one of the nine.
- func_8000FAC4's remaining 10 is one pointer in `$v0` where the target has `$a0`, at a store inside a
  `do { } while (0)`; about 20 spellings of that store (inline setter, register local, volatile, loop forms) gave 10 or
  worse. This part is one function.
