---
title: object byte offset across calls
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A large MIPS rendering function indexes a record array, reads a byte field to
index two coordinate arrays, and calls drawing routines between those reads.
The target keeps the record's byte offset in a saved register across calls.
After a paired-coordinate address rewrite, the compiler instead recomputed
that offset and added eight instructions.

Keeping `(index & 0xffff) * sizeof(Record)` in an unsigned local and using it
to address the record at each original read retains the offset without caching
the field across calls. With KMC GCC 2.7.2 and the flags above, a read-only
local compilation and the app's linked scoring code reproduced the app's 655
baseline and scored the combined change 620. The paired-pointer one-shot scope
is matching scaffolding. The app confirmation of 620 was pending at the time
of this observation; it is not a confirmed match.

Moving the offset's initialization outside the branch or staging the other
shared array's base in a pointer produced larger scores. The third coordinate
site needs a different allocation: applying the same paired-address shape
there raised the score. These are observations on one function, not a
controlled compiler rule; the register allocation mechanism remains a
hypothesis.
