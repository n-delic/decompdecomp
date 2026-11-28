---
title: reused coordinate address
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A large MIPS rendering function computes two real halfword-coordinate
addresses. Its app-confirmed O1 diagnostic score was 350, with register
allocation differences at those addresses.

On the 32-bit MIPS compiler and flags above, reusing a dead signed 32-bit
scalar for the first address, then converting it back for the actual
halfword read, changed the linked local comparison to 345 at either of two
sites and 340 at both. The integer pointer cast and the one-shot assignment
scope are matching scaffolding. The scalar's previous value is not observed
and its later original use resets it. The app subsequently confirmed the 340 diagnostic; the function
was still open.

Reusing a pointer local for the other address or using the integer local at
the third site raised the score. These are observations on one function, not
an exact match or a controlled compiler rule.
