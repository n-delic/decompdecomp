---
title: fold pushes a binary op into a conditional operand, so a joined conditional needs a conditional sibling
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3
evidence: observation
date: 2026-11-23
---

fold-const.c (2.7.2, `fold`, the `TREE_CODE (arg1) == COND_EXPR` / `arg0 == COND_EXPR` cases) rewrites `A op (c ? x : y)`
and `(c ? x : y) op B` into `c ? A op x : A op y` for every binary operator, always; a non-trivial other operand is
first wrapped in a SAVE_EXPR so it is evaluated once, before the branch. Two consequences for a word built as
`S | T` where `S` is a two- or three-armed conditional (a scissored texture rectangle's s correction):

- with `T` a plain term (`t & 0xFFFF`) the `|` lands in every arm of `S`: the arms end in their own stores and cross-jump
  into one tail; the target keeps `S` joined (one register) and ORs once after the join.
- with `T` a conditional too, `fold` takes the `arg1` case first: `S` becomes the saved operand (evaluated and joined first)
  and `T`'s arms carry the `|`. If `T`'s condition is provably false later (combine knows the sign bit of a zero-extended
  16-bit value times 8 is clear) the arms vanish and only the joined `S` and one `or` are left.

The sprite drawer that showed it passes the same macro to its high-resolution and its low-resolution arm; in the
high-resolution arm the second operand's condition is `((u16) y) * 8 < 0`, never true. Giving that arm the same two-conditional
macro as the other (instead of a `t & 0xFFFF` term) took the function 6035 -> 3175 (exact judge, isolated), the largest
single step of a 664-instruction rewrite.

One function; the fold-const.c reading is from the source, the combine step is hypothesis.
