---
title: A dispatch's later arms through an array pointer need three edits together
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: controlled
date: 2026-11-26
---

What the case looked like: an N64 neighbour check, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, where the first arm
reads a two-element `extern s8` array through one pointer register and the later arms take its address again
(`lui; addiu`, then `lb 1(reg)`), with the first arm's `|` test tail kept and the last arm jumping back into it.

The source change (library family `else_arm_symbol_pointer`), measured with the app's judge: 740 to 0.

- `do { p = T; } while (0);` at the start of the `else`, the later arms reading `p[0]`, `p[1]`;
- `if (A || B) f = 1;` among them written `if (A) f = 1; else if (B) f = 1;`;
- the whole `if` / `else` chain in `do { } while (0);`.

Failed experiments: any two of the three score 250 to 990 (all eight combinations scored); a plain `p = T;` is folded
back into the symbol's own address for `p[1]` (250); `register`, `*(p + 1)` and a two-member struct change nothing.

Controlled: the removal of each edit in turn on one function. That the loop note stops the folding is a hypothesis.
