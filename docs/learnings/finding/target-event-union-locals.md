---
title: Target-proved event payload views
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

One MIPS audio event dispatcher passed the address of a draft local while other guessed locals represented fields of the same stack event. The target stores a halfword at the event base, a pointer at +4, a word at +8 and a channel byte at +12. Another event stores individual bytes at +8, +9 and +10. A matched neighboring dispatcher independently uses the same halfword, pointer, word and channel layout.

After correcting a separate pointer read that occurred before initialization, rebuilding those target-proved aliases as a record with a union for the word and byte payload improved the linked judge from 17198 to 15661. The app saved that source as its new best; this function was not yet matched. This fixes an incomplete decompiler draft rather than claiming that arbitrary independent locals are equivalent. A declared repair checks the target stack evidence and rejects independent alias reads, and its generated child independently scored 15661. Synthetic tests cover that witness and the guards, not other game matches.
