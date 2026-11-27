---
title: a field saved to a previous-value field and then advanced by another field, written as `F += H`
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-27
---

A 54-instruction N64 object update: a speed field is stepped, the position is saved to a previous-position field and
advanced by the speed. The target copies the position's register (`move`), stores it to the previous field, loads the
speed again after that store and adds position first. The saved best (205) read the position into a local early and
stored `t + speed`; it was missing those two instructions and had the add's operands swapped.

The source change: `pos = t + speed;` (where `t = pos;` and nothing in between writes `pos` or `t`) written
`pos += speed;`. With the search's one-shot `do { } while (0)` fences left in: 205 -> 60. With the fences opened as
well: 0 (the app's judge). The plain, typed form `p->speed += K; p->prev = p->y; p->y += p->speed;` also matched at the
first try.

Failed: `p->y = p->prev + p->speed;` and `p->y = (p->prev = p->y) + p->speed;` both scored 485; moving the early load
down into the previous-field store with the fences kept scored 305. Earlier agents tried statement reordering and
temporary copies (13 attempts) without reaching it. That the compound's own memory read is what keeps the reload is a
hypothesis; the compiler source was not read.

decomp-repair `copied_field_compound` (and `copied_field_compound_unfenced`). One function; the measured scores are
controlled.
