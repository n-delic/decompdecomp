---
title: array index from one
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-25
---

What the functions looked like and the exact compiler and flags: two vector routines built by SN GCC 2.8.1 (`-G0
-mips3 -O2`) with a local `Vec3 pts[5]` of which the code uses `pts[1]` .. `pts[4]` (loops `for (i = 1; i < 5; i++)`,
`&pts[i]` passed to callees).

The rule: the frame places the used elements one element higher when the array is one longer and its first element is
never used; every stack row differs by the element size and nothing else says why. A draft with `pts[4]` indexed
from 0 has the same frame size and every row off.

Measured with the fast judge (read-only) on drafts reconstructed by shifting the matched source's array and indices
down by one: 2453 -> 0 and 2743 -> 0. Offered by decomp-repair `array_index_from_one`: `T a[N]` as `T a[N + 1]` with
every `a[i]` / `&a[i]` as `a[i + 1]`, only when no bare `a`, `&a` or `sizeof(a)` appears.

Failed experiments that would otherwise be repeated: the other array of the function (a different array shifted
alone) scored worse (2424, 2711, 2867): one child per array.

What is controlled evidence and what is one function: two functions of one game, both reconstructions.
