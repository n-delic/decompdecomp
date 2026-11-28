---
title: equality ladder chain from machine draft
kind: function
compiler: SN GCC 2.8.1 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 34-instruction N64 page selector. Six
values of one argument, each storing two globals. The machine draft was a nested tree of `==`/`!=` tests with a `goto`
into one arm and a local (`1`, later `5`) that the shared stores read: the target's cross-jumping and register reuse
read back as source. Symptom: one register row (`sw $a0` where ours is `sw $v1`), jumps into another arm's stores.

The source change, and the measured result before and after (scores, the judge used): a flat `if (x == 1) {..} else if
(x == 2) {..}` chain in test order, each arm with its own two stores. Machine draft 565, chain 0 (the app's judge), with
the argument stored as its number or as the argument (both 0 in the isolated judge). decomp-repair
`equality_ladder_chain` writes both children from the machine draft.

Failed experiments that would otherwise be repeated: `goto_shared_tail` alone reaches 5 and stops there. Keeping the
nested tree and replacing the local by its numbers scored 10, with or without the argument written as numbers.

What is controlled evidence and what is one function: one function. That the compiler's cross-jumping makes the shared
blocks is a hypothesis; the pass dumps were not read.
