---
title: Byte copy in a later switch arm
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 534-instruction MIPS handler loads two bytes before switching on a command.
Its control-change case uses the second byte repeatedly. Using a same-type
copy of that byte in that case moved an isolated gcc-2.7.2-kmc judge from
3280 to 3175 with `-G0 -mips3 -mgp32 -mfp32 -O2`. Copying only in the
note-on, pressure or bend cases left the score at 3280. The app judge request
was still running when this evidence was recorded.

The `switch_value_copy` repair was extended to later cases only when the
previous case cannot fall through into them. The score is an observation on
one function; its cause in compiler register allocation is a hypothesis, not
confirmed by a source-level compiler analysis.
