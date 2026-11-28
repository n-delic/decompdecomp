---
title: reused coordinate byte index
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A large MIPS rendering function calls a width routine and uses a record's byte
field as a coordinate-array index. Its target uses a different register for
that byte than the compiler's candidate. The app-confirmed O1 score was 410.

Reusing an existing scalar local for the actual index, where its previous
value is dead and its next original use resets it, changed the read-only
linked score to 390 at each of three sites and to 350 at all three. A comma
expression keeps the original argument evaluation order and reads the reused
local as the real index; that spelling is matching scaffolding. A separately
staged source with the same linked 350 score was submitted to the app, whose
result was pending. The function was still open.

Casts, an alternate table alias, and an inline helper tied at 410. Reusing a
loop counter or a signed-byte local made the score worse. This is one observed
function, not a controlled compiler rule.
