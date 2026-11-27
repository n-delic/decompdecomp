---
title: libm sin and cos need double literals and KMC GCC -O1
kind: finding
compiler: gcc-2.7.2-kmc -O1
evidence: observation
date: 2026-11-27
---

Four sine and cosine routines (double and float, 68 to 76 instructions) in an N64 game whose default profile is
SN64 GCC 2.8.1: `n = (int) (0.0 <= x ? y + 0.5 : y + -0.5)`, a Horner polynomial, a negation on `n & 1`.
Profile that matched: `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, applied per function.

Measured with the app's judge on the cosine: the project profile with `extern double` pool words scored 2550;
KMC `-O2` 1890; KMC `-O1` with externs 650 to 950; the same C with the pool words written as double literals 0.
The float cosine then needed `w = z * z * (poly)` in one expression (20, four register rows, to 0).

Failed: IDO 7.1 (6805 and up), SN GCC 2.7.2 (1450), KMC `-O2` and `-O3`, `extern double` for the constants at any
level, `if`/`else` into a `double t` before the conversion (20 rows of difference in the double sine and float sine
until written as the one ternary).

Controlled: the extern-to-literal step on one function. The profile was then confirmed exact on all four.
The library's `double_literal_global` already offers the literal; it does not combine with the profile change.
