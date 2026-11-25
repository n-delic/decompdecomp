---
title: an integer absolute value is abs(), not a copy and a conditional negate
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp32
evidence: verified
date: 2026-11-25
---

What the function looked like: distance and proximity tests of an N64 game: `dx = (s32)(a - b); if (dx < 0) dx = -dx; if (dx < 201) ...`.
m2c writes the absolute value as a copy and a conditional negation, which GCC 2.8 compiles to `bgez` + a copied register + `negu`.
The target has `bltzl $v0, L` with `negu $v0, $v0` in the likely branch's slot, the code `abs()` expands to.

The source change, and the measured result before and after (scores, the project judge): `int abs(int);` and `v = abs(expr)`
(or `v = abs(v)` for a bare test). Matched functions wrote some tests as `abs()` and others as `(d < 0) ? -d : d`, so no all-sites
rule is exact; the library family `abs_idiom` (decomp-repair 0.5.1044) offers every site and each site alone. On open functions:
6742 -> 6086, 8410 -> 7905 (all sites), 40895 -> 40660 (one site), 35854 -> 35339; two of seven did not move or got worse
(5122 -> 5442).

Failed experiments that would otherwise be repeated: `abs` on a float variable (not the same insn); rewriting when the target has
no `negu` (nothing to negate).

What is controlled evidence and what is one function: observation over several matched functions (twenty matched drafts use
`abs()` in the final source where the draft had the negate), partial gains on open ones.
