---
title: A float constant used as call arguments sits in a float and an integer saved register at once; write one local per argument position
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

A 556-instruction N64 function (`gcc-2.7.2-kmc`, `-O2 -G0 -mips3 -mgp32 -mfp32`): a split-screen setup routine calling one
rectangle helper (`obj, x, y, w, h, ox, oy`, seven float arguments, three in `$a1`..`$a3`, four on the stack) from a dozen
places with the screen's half sizes, `160.0f` and `120.0f`. Saved best 11936 (14156 re-judged): one `f32 centerX = 160.0f;`
and one `f32 centerY = 120.0f;`, 302 differing rows, nearly all registers.

What the target does: each half size is in TWO saved registers for the whole function, one float and one integer, the
second a copy of the first made in the prologue (`lui $at,0x4320; mtc1 $at,$f26 ... mfc1 $s6,$f26` for one constant,
`lui $fp,0x42f0 ... mtc1 $fp,$f30` for the other: the first-defined twin is the constant load). The integer twin is read
where an argument goes to an argument register (`move $a2,$fp`) and in sums (`mtc1 $s6,$f6; mtc1 $s6,$f8; add.s`), the
float twin where `mfc1 $a1,$f26` or a stack argument (`swc1 $f30,0x10($sp)`) or a product needs it. One local per constant is
one pseudo and one register, so every saved register after it is shifted and the sums read the wrong copy.

Fix: one local per call-argument position that reads the constant (four here: x, y, width, height of the helper, in position
order), each initialised with the same literal; a sum or product inside an argument names that argument's twin, the
`half = cx * 0.5f` locals the twin of their own position. The twins alone took the source with the other scaffolding
(14156 re-judged) to 14037 but made the prologue the target's; with the items below, in this order, 14037 -> 12072 -> 5980
-> 5540 -> 4500 (302 -> 116 differing rows). The first basic block's reads are canonicalised by cse, so only the later
blocks' reads need to name the right twin.

Together with it:
- the identical-arm `if (table) { call } else { call }` round the first null-check call (jump.c's skipped-block rule in cse
  needs a label-free block with one jump label; the arms make the block unskippable, so a value converted before it is
  not reused after it);
- `D_8010A4C3` reached through an identity inline (`return p + 0;`) for the `[0]` byte and plain `&D_8010A4C3 - 2` for the
  other (a base register for both instead of two folded symbol+offset addresses);
- separate `case 3:` and `case 4:` bodies (the target compares for each, with its own null checks);
- `x = 0; if (osTvType) { y = 0; x = y; } else { y = 0; }`: with both zeroes before the test cse deletes the empty
  conditional and its load; assigned in the arms, reorg moves the common `y = 0` into the branch's delay slot
  (5540 -> 4500 alone; library `constant_copy_arm_assign` writes it and reproduced the 4500 from the 5540 source).

Library: `float_role_twins` writes the first fix (children: every eligible local at once, reads outside calls to the lowest
or the highest position; then each local alone) and `float_twin_toggle` renames one read to a sibling twin, one child per
read. From the plain one-local source: 17825 -> 16436 (role twins) -> 5155 (greedy chain of toggles, exact judge); the
hand-made assignment reached 4500, so a single-read toggle chain is close but not equal (pairs are the next step).

Not found by any family yet (left for the next run): the first table entry read as its own scalar symbol in one block
(`D_80109688` instead of `sTrackMarkerTable[0]`, which stops cse making a base register for it there, 5980 -> 5540: the
symbol name is the app's to know, not the library's); the unsigned-to-double conversions of the second and later
argument lists are recomputed in the target but reused by cse here (a plain `(float:DF y)` is one expression; the
target's `y` calls 1 and 2 are recomputed although the constants beside them are reused); and `x = 0` sits early in the
target's first block where ours sinks it into the branch's delay slot.

Failed: literals instead of locals (23286), `register` on either local, swapping the declaration order of the four
(14466), the same twins in every read in the same order as the first guess (16436).

Controlled evidence: one function; the allocation explanation (two pseudos ranked apart by `floor_log2 (refs) * refs / live`)
is a hypothesis from the dumps, not read in global.c.
