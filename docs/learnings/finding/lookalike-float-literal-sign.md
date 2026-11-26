---
title: a look-alike with another float constant (or its sign) differs in one lui; the literal search now writes it
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 93-instruction N64 function with a matched
sibling in the same game built with `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (the open function's own profile was
the -O2 default). The sibling's C, its globals renamed, differed from the target in one word outside relocations:
`lui $at,0xc1a0` against `lui $at,0x41a0`, a `* -20.0f` that is `* 20.0f` in this one.

The source change, and the measured result before and after (scores, the judge used): the transplant scored 150 under
the sibling's profile; with the literal rewritten it is exact (app judge, and `suggest` applied the profile to this
function only). The look-alike literal search (`app/lookalike_profiles.py`) rewrote integer literals only; it now
also rewrites `f`-suffixed float literals by their bit pattern, taking the unary minus with them. Run on the transplanted
source with the real compiler, it reaches 0 with one compile.

Failed experiments that would otherwise be repeated: the saved best (search-made scaffolding, 757) and every earlier
small-model attempt kept a hand-computed record offset; the plain sibling struct was the whole difference.

What is controlled evidence and what is one function: the substitution is covered by a unit test; the match is one
function. A binary subtraction (`x - 2.5f`) is built from +2.5, so its minus is left alone.
