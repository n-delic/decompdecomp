---
title: narrow record coordinate address
kind: finding
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A large MIPS rendering function masks an object index to 16 bits and uses it
to compute paired coordinate addresses. Its app-confirmed O1 diagnostic was
340, with register differences at three draw sites.

Storing the bounded index in a `u16` local, together with reusing a dead
32-bit scalar for the first actual coordinate address, changed the app's
diagnostic from 340 to 320. Narrowing alone tied at 340; address reuse alone
raised the local linked score to 425. The integer address cast and one-shot
scopes are compiler-matching scaffolding. This is one observation, not an exact
match or a controlled compiler rule.

The candidate has the target instruction count and four displayed register
rows at the first two draws. The target's other two displayed operand
annotations encode the same linked immediate values. On this case, GCC's
local allocator ties the second address to the table-base register as that
base dies; the target uses a different register.
