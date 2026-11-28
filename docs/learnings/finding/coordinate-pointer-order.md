---
title: coordinate pointer assignment order
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A large MIPS rendering function uses two independently staged addresses for
adjacent halfword coordinates. A one-shot scope surrounds the assignment of
the first pointer, followed by the second pointer's assignment. Both pointers
feed the original coordinate reads.

Keeping the index and base calculations in place, but assigning the second
pointer inside the existing scope before assigning the first, changed a
read-only linked comparison from 620 to 615 at either of two sites and to 610
at both sites. That comparison independently reproduced the app-confirmed
655 starting score; the app subsequently confirmed the 610 diagnostic. The one-shot scope
is compiler-matching scaffolding, not an assertion that the original code
used this shape.

Moving only the scope to the second assignment or reversing addition operands
raised the score. This is one observed function and does not establish which
compiler allocation pass accounts for the difference.
