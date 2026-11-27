---
title: A display-list command m2c stores after the arms, through the moved cursor
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 128-instruction N64 mode switch that appends one display list per arm (gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O1`,
exact). The target keeps each arm's old cursor in `$v1`, jumps to one shared `sw $v0, 4($v1)` and returns. m2c writes
that store once after the switch as `(*gfx)->unk4 = v`, which stores into the next command.

The repair is `cursor_join_word_arms`: store in each arm through the arm's own pointer. With `switch_if_chain` and
`default_neighbour_case` (the target's compare tree has a `slti` for a value no arm names: `case 1:` beside
`default:`), `prove_chain` goes from the machine draft to 0: 9980 -> 4480 -> 2040 -> 0 (the app's judge). By hand,
the gbi-style macro `{ Cmd *g = (*gfx)++; g->w0 = ..; g->w1 = ..; }` in every arm plus `case 1:` is the same match.

Under `-O2` the same source scored 1760 (the cursor's table index is scheduled into the first command). Controlled
result for this one function; the `-O1` profile is the one its neighbours use.
