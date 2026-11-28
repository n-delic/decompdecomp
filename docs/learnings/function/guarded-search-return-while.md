---
title: m2c's guarded for(;;) search is the while that returns what it finds
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 13-instruction N64 string search (find the
first byte equal to an argument, else return null), its own profile KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O1`. m2c
drafted it as `if (*p != 0) { for (;;) { if (*p != c) { p += 1; if (*p == 0) { return NULL; } continue; } break; }
return p; } return NULL;`.

The source change, and the measured result before and after (scores, the judge used):
`while (*p != 0) { if (*p == c) { return p; } p += 1; } return NULL;`. 360 as drafted, 0 after, with the app's
standalone judge and recorded exact by the app. The draft's local copy of the parameter can stay. The decomp-repair
family `guarded_search_return` writes this child from the draft (its one child scores 0).

Failed experiments that would otherwise be repeated: `prove_chain --families all` with decomp-repair 0.6.497 found no
improving child of the draft (31 judged). The draft scores 360 at both -O2 and -O3 as well (-O0 3640): a profile
change does not close it.

What is controlled evidence and what is one function: one function. The rewrite is the same program (same tests, same
returns); that it is exact is measured only here.
