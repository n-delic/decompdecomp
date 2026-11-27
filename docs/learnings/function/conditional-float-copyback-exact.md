---
title: conditional float copyback exact
kind: function
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 1,558-instruction N64 function was matched by the app with GCC 2.7.2 KMC and `-G0 -mips3 -mgp32 -mfp32 -O1`. The previously configured `-O2` profile scored 45,590 on the saved near-match; the exact source and profile were verified and saved together.

At a 15-point diagnostic, the only differences were three registers in one conditional float negation. The target copied the input to a temporary in the branch delay slot, conditionally negated that temporary, then copied it back. Rewriting `if (x > 0.0f) y = x; else y = -x; x = y;` as `y = x; if (!(x > 0.0f)) y = -y; x = y;` reached zero. The transformation preserves the non-positive and unordered paths, including signed zero. A repair emitted an independently judged exact child from the 15-point source.

Earlier diagnostic steps used non-overlapping float locals across a cubic value and call arguments (505 -> 195), passed a literal directly to a rotation call (195 -> 60), staged the square of a float before the cubic product (60 -> 50), put a common store into both arms (50 -> 25), and staged a field load inside both arms (25 -> 15). These scores are measured app-compatible judge results for this function. Related synthetic tests check the reusable repair guards; they are not independent game matches.
