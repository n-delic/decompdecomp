---
title: SDK audio source with a driverless synthesizer ABI
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 compact MIDI handler resembled a routine in a locally installed SDK source, but its target called driverless synthesizer functions rather than passing the player driver. The source also required destination-specific symbol mapping and one pan override; a hand-adapted typed reconstruction scored 1396 in the ROM judge, compared with the earlier saved 1989 candidate. This is a near-match, not an exact SDK identity or a controlled match.

For a controlled repair check, a synthetic standard-driver form of that reconstruction was made by restoring the player driver argument to its volume, pan and pitch calls. The `sdk_audio_driver_calls` repair produced a child scoring 1396 in both draft and local lanes. It requires corresponding target call instructions and compatible destination declarations, and excludes volatile driver reads and opaque macros. This validates the ABI-call rewrite only; finding, mapping and adapting the complete SDK source remains separate work.
