---
title: wide constant shift split
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-26
---

What the function looked like and the exact compiler and flags: a 4-statement scaler (a game's address offset times a
shift-and-add chain, one return) in an N64 game built with KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2`. m2c's draft
multiplied by a constant wider than 32 bits whose low bits are all zero.

The source change, and the measured result before and after (scores, the judge used): `(e) * -0x22222222000` became
`((e) * -0x11111111) << 13`. The exact judge (`fast_judge.py`) scored the draft 215 and the rewrite 0. The library's
`wide_constant_shift_split` family produces it from the draft (`prove_chain.py`: 215 -> 0 in one step, two children).

Failed experiments that would otherwise be repeated: no existing family changed the 215 draft (12 children, none better);
a shift of 32 or more is undefined for a 32-bit value, so the family never offers one.

What is controlled evidence and what is one function: one function (verified 0). The explanation (the multiply expander
works on the constant's low word) is a hypothesis. The equality of `e * (K << n)` and `(e * K) << n` modulo 2^32 is
arithmetic, tested in the library.
