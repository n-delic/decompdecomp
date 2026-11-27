---
title: (X - K) - Y held as a block-scoped local so cse does not reassociate it
kind: function
compiler: KMC GCC 2.7.2 -O1 -g and SN GCC 2.7.2 -O1 -g
evidence: verified
date: 2026-11-27
---

What the function looked like: a 59-instruction N64 display-list setup that stored `Y + 0x11310` (Y a global word) and
then, in two later statements, `((X - 0x11310) - Y) >> 3` (X a global word 0x1C below Y's record). 440 with the difference
written inline, on both compilers.

The source change, and the measured result: each of the two statements written
`{ s32 span = X - 0x11310; STATEMENT-with-span - Y; }`, a block of its own for each: 440 -> 0 on KMC and on SN 2.7.2 (the
app's judge). Controlled variants of the same text: `span` named inside the expression (`(span = X - K) - Y`) 70, one
function-level `span` assigned twice 70, a block-scoped local each 0. The compiler folds the constant out of
`(X - K) - Y` and reuses the `Y + K` of the first statement; a named pseudo with its own block is not folded through
(hypothesis from the bytes, not read in cse.c/combine.c). Now `difference_block_stage` (decomp-repair), one of its six
children from the 440 source.

Failed experiments that would otherwise be repeated: the app's families `named_sum_difference` (inline assignment) and
`reassociate` do not reach it, and the inline-assignment form is a 70-point near miss, so the block matters.

Controlled evidence and what is one function: one function on two compilers; operands were `M2C_FIELD` word reads.
