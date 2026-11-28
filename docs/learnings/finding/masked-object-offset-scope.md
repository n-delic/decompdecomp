---
title: masked object offset scope
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A large MIPS rendering function masks a record index to 16 bits, scales it by
the record size, and retains the byte offset across drawing calls. The app's
O1 diagnostic score was 610; the function was still open.

Staging the masked index in a used `u32` local, then assigning the used offset
inside a one-shot scope, changed the read-only linked comparison from 610 to
550. The source retains field reads after calls. The scope is compiler-matching
scaffolding. The app confirmation was pending when the observation was made;
the function did not match.

Scoping the masked-index assignment instead scored 705; a narrow-index local
without the offset scope tied at 610. This is one function, not a controlled
compiler rule. Which allocation pass accounts for the difference is unknown.
