---
title: mirrored sibling transplant needs per use edits
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a set of four rotations of one screen update
(181-182 instructions, KMC GCC 2.7.2 -O1, while the project default is -O2). Each reads two byte globals in the same
places; they differ in the callees, which global gets `+ 1` or `- 1` before the float conversion, and which one the last
branch tests (`!= 0` or `< 23`).

The source change, and the measured result before and after (scores, the judge used): one matched sibling's C with this
function's names (`twin_drafts.transplant`), judged under that sibling's -O1: 465, 250 and 565. decomp-repair
`global_site_forms` (each use of a loaded global written as the instructions after the target's load say) makes one exact
child for each. The similar-code pass judged these transplants under the donor's profile but only searched literals
there; it now runs this family on a near miss under that profile (`similar_matches`).

Failed experiments that would otherwise be repeated: hand-written candidates and Deep Permuter lanes under the
function's -O2 settings stayed at 2000 to 9000. `rom_renames` renames a donor's global everywhere when only some of its
uses read another global (the uses at the same address are not recorded as evidence), so a transplant can lose a symbol
entirely; the family declares it again.

What is controlled evidence and what is one function: the three transplant results were measured with the app's judge;
all three functions are from one set of siblings.
