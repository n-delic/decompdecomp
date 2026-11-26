---
title: volatile record members keep a store run above a flag read while a global load passes them
kind: finding
compiler: sn-n64-281
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 235-instruction N64 function, SN64 GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32`. Near its end
it stores a callback and -1 into a camera record, reads the record's flag word, stores zero and two floats, then ORs the
flag word. The target loads another global pointer (the next call's argument) above both of the first stores.

The source change, and the measured result before and after (scores, the judge used):
The app's judge. Saved best with the -1 as a raw offset store: 180 (the global load stayed below that store). The -1 as
a member store: 525 (the flag word's load rose above both stores). The member store and `volatile` on exactly the three
members (callback, -1 field, flag word) in the record typedef: 0. Library: decomp-repair `volatile_member_pin`; its
first child on the 180 source scores 0.

Failed experiments that would otherwise be repeated:
All best-or-worse alone: `volatile` casts on the one store (180), one or two volatile members (525), every statement
order (120 at best, with one more raw store), bit-field, union, array and nested spellings of the flag word (525), the
global pointers typed as records or arrays (180 to 740). A fourth volatile member gives 60, a fifth 120.

What is controlled evidence and what is one function:
One function. That a plain member store never aliases a scalar global while two volatile references always depend on
each other is the scheduler's documented rule in GCC 2.7.2 (`sched.c`); that it explains this case is a hypothesis for
2.8.1.
