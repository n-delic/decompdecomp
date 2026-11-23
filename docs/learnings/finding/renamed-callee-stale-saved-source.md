---
title: renamed callee stale saved source
kind: finding
compiler: gcc-2.7.2-kmc (any; the judge's link step)
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:
An 842-instruction N64 lighting function (KMC GCC 2.7.2) whose saved best sat at 300 for a long time. Its only two
differing rows were calls: the saved C called a helper by the name it had before the project renamed it, so the name
did not link and the judge charged each call site.

The source change, and the measured result before and after (scores, the judge used):
Renaming the two call sites to the target's name: 300 -> 0 (app judge, byte-identical). decomp-repair
`renamed_callee_refresh` (PR 1454) pairs a callee the function calls but the target never names with the unclaimed
`jal` target of equal call count and renames it everywhere; run on the stale source it returns the matched source.

Failed experiments that would otherwise be repeated:
None; do not search spellings or order on a function whose rows are all `jal` operands: read the callee names first.

What is controlled evidence and what is one function:
One function; the family's unit tests are controlled.
