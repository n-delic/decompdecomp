---
title: target stack event record
kind: finding
compiler: GCC 2.7.2 KMC -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction N64 sequence handler compiled by GCC 2.7.2 KMC with `-G0 -mips3 -mgp32 -mfp32 -O2` posts an event several times. The target uses a halfword at stack offset 0x20, words at +4 and +8, and a byte at +12, and repeatedly passes the address at 0x20. A draft declared these as unrelated locals and passed the address of only the first.

Grouping the event fields into one 16-byte record and leaving an unrelated later use of the original scalar intact improved the app-compatible judge score from 10,803 to 10,162. The target evidence shows the required layout; the rewrite repairs the draft's object layout rather than preserving the incomplete draft's independent local addresses. Synthetic tests check the record, repeated target address, observed stores and rejection of shadowed names. This is one observed function, not a match or controlled compiler regression.
