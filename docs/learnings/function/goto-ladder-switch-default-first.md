---
title: goto ladder switch default first
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3
evidence: verified
date: 2026-11-23
---

A 141-instruction N64 function that maps a selector to a replacement value (about twenty selector values, nine
results, a "no case matched" flag read by code after it). m2c wrote it as a goto ladder: compare splits (`id < 0x154`),
shared tail blocks (`to2FC: id = 0x2FC; goto joined;`), `other = 1` set at each failed leaf, and a `finalCase`
temporary so one compare serves three values. Saved best 660. KMC GCC 2.7.2, `-O2 -G0 -mips3`.

The three differing places were `beql` (taken: the arm's `li` fills the annulled slot) where the ladder gave `bnel`.
Moving the `then` arms out of line with `goto` labels fixed three of the four and left the id == 2 one at 220, because
jump2's cross-jumping merged that test with the other test that jumps to the same label (a shared tail only forms when
the labels are identical). Reading jump.c's `find_cross_jump` showed the original's tree was not an if ladder at all.

Written as one `switch (id)` (cases grouped by result, `default: other = 1; break;` FIRST in the source), the compiler's
own decision tree, cross-jumping and delay-slot filling reproduce the original exactly: 660 -> 0, a 141-instruction
match. Both layout facts matter. With `default:` last the function scored 1550: the default block then falls into the
code after the switch and the leaf tests jump to it directly, where the original's leaf tests share a
`li $a2,1; j` tail. The arms' order is the order of the blocks after the switch: ladder order matched; ascending order
was not tried alone because ladder order already matched. Cases that share a result share an arm; the compiler merges
equal-result tails itself, separate arms for each value scored the same.

Failed: `goto` labels for the then arms with distinct labels per site (750: the identical blocks merge, so the tests do
too); a clean if/else rewrite with the arms swapped (worse: the whole block reorders); `else`-first and goto-to-shared
variants of the id == 2 site (750).

Controlled by the library family `goto_ladder_switch` (decomp-repair, PR 1316): from the saved best its first child
(default first, arms in ladder order) scores 0 in the app's judge, the other three children 110, 1300 and 1550.
One function; the rule (a decision tree the compiler built comes back only from a switch, with default: first) is the
hypothesis to test on the next goto ladder over one variable.
