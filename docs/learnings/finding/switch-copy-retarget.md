---
title: Retarget a switch byte copy
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler copies an incoming byte and reads the copy in
one switch case while other cases read the original. Moving those copy reads
to a different case and restoring the original reads in the first case keeps
the value unchanged. With gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`, an
isolated judge score changed from 2825 to 2745. `switch_copy_retarget`
produces the exact candidate and checks case fallthrough and intervening
writes. The app judge request was still running when this was recorded.

Using the copy in both cases scored 3326; moving it to several combinations
of other cases scored 2755 to 3326. These are observations on one function,
not controlled exact-match tests. The register-allocation explanation is a
hypothesis rather than a verified compiler-source finding.
