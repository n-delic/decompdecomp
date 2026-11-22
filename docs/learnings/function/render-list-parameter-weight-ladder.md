---
title: render list parameter weight ladder
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function looked like and the exact compiler and flags: an 88-instruction render-list walk (an N64 game, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`): an outer loop over objects, two inner loops over their child lists, a "first row" flag that makes two set-up calls once per object, and eight calls that take the one parameter. The saved best (110) had every row right except that `$s1` (the parameter in the target) and `$s4` (the flag) were exchanged, and the flag read through a copy (`v0 = flag` at each loop head and end) that cse folds to a constant unless the flag's initializer is hidden (`flag = (a != NULL || b == b)`, found by the permuter earlier; scaffolding, kept).

The source change, and the measured result before and after (scores, the judge used): the `-dl -dg` table (`passes.py`) showed the parameter at 21 references over 63 insns (1.333) below both list pointers (1.692, 1.419) and the flag (1.744). The target's order is parameter, child pointer, object pointer, flag. Wrapping calls that name the parameter in `do { ... } while (0);` adds a reference each (hypothesis: flow.c counts a reference once per loop depth). Judge-worker scores from the saved best: every single wrapper 110; all eight wrapped once 110, twice or more 105 (the pointers sharing those calls rise as far as the parameter); the last seven calls two deep, or the last four, five or six calls three deep: 0; found with a 3^8 grid of per-call depths (6561 compiles, many zeros, the first one `(0,2,0,0,2,2,1,2)`). decomp-repair `argument_weight_scope` now offers the last k calls wrapped together at depths 2, 3 and 1; `prove_chain --families argument_weight_scope` goes 110 -> 0 in one step.

Failed experiments that would otherwise be repeated: the flag initialized to the literal 1 (360, then 200 with the wrappers; the copy becomes `li $v0,1`: the one remaining row); `(a != NULL)`, `(a != NULL) | 1`, `a != NULL || c != NULL` (400, 200, 665); a natural nested `for` rewrite without the flag copies (885: the copies are what the target has); wrapping every call (105).

What is controlled evidence and what is one function: one function; the saved-best start and the wrapper counts are measured, the priority explanation is a hypothesis not read in global.c/flow.c. The match relies on scaffolding: the `|| var_s2 == var_s2` in the flag's initializer and the nested one-shot wrappers (both compile to no instructions).
