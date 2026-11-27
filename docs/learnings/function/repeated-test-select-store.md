---
title: a repeated test that picks a stored value is two stores, not a temporary (register rows $a0 for $v0)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 233-instruction N64 object update, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Under `if (mode == 1)`
it spawns objects; then it stores one of two constants in a field by the same test. The target branches from the first
test straight to the store with the other constant in the delay slot (jump threading, read from the instructions). m2c
read it as `t = -0xE66; if (mode == 1) { ...; t = -0xE66; if (mode == 1) { t = 0xE66; } } p->turn = t;`. Four register
rows were left: the value in `$a0`, the target's in `$v0`.

The source change, and the measured result before and after (scores, the judge used):

`if (mode == 1) { ... } if (mode == 1) { p->turn = 0xE66; } else { p->turn = -0xE66; }`, the temporary gone and the two
`do { } while (0)` fences a search had added removed: 20 -> 0 (app's judge). decomp-repair `repeated_select_store`
writes it as one child from the saved best, and from m2c's labelled form (store under a label in the outer `else`),
which took the draft from 3125 to 2765.

Failed experiments that would otherwise be repeated:

With the fenced guard kept and the two stores written: 370. With `if`/`else` filling the temporary and one store: 515.
A conditional expression stored after the block (`p->turn = mode == 1 ? 0xE66 : -0xE66;`): 515; with `!=` and the arms
exchanged: 855.

What is controlled evidence and what is one function:

All of it is one function. Why the temporary takes another register than the two stores' value is a hypothesis.
