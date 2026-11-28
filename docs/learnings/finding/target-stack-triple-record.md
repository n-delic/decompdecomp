---
title: Target-proved three-field stack record
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A MIPS audio dispatcher passed the address of one guessed stack local to a voice initializer while its two neighboring locals were also written for that call. The target writes halfwords at offsets +0 and +2 and a zero byte at +4 before passing the record base. The matched neighboring dispatcher uses a three-field voice-configuration struct with that layout.

Replacing those locals with one record, while correcting the volume helper's declaration to its matched 32-bit return type, improved the linked judge from 15661 to 15571 and was saved as the new best. The function remained open. This reconstructs an incomplete draft using target evidence, rather than treating arbitrary independent locals as interchangeable. A declared repair independently produced the 15571 result from the corrected-prototype source, checking the target witness, shared source scope and zero-only byte writes. Synthetic tests cover its guards, not additional game matches.
