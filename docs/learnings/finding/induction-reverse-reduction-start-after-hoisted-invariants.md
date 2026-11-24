---
title: induction reverse reduction start after hoisted invariants
kind: finding
compiler: sn-n64-281 (KMC GCC 2.7.2) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags:
A 111-instruction N64 menu-drawing function (sn-n64-281, KMC GCC 2.7.2, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300
-mhard-float -mfp64`) with a loop over a counter that draws one row per entry at `0x50 + 10 * i`. m2c's draft read the
reduced variable as its own `var_s1 = 0x50` stepped by `0xA` in three places (one per jump of a compare chain over the
counter, a switch the compiler had made a decision tree of) and kept the loop as `if (D > 0) { do { ... } while }`.
Saved best 60 (three differing rows): the target has `addiu $s1,$zero,0x50` after the hoisted invariants
(`addiu $s5,$zero,1`, `addiu $s4,$zero,2`, `move $s3,$v1`), the draft has it before them.

The source change, and the measured result before and after (scores, the judge used):
GCC's loop pass strength-reduces `base + i * K` and writes the reduced variable's start just before the loop label,
after the hoisted invariants; an explicit `y = 0x50; ... y += 10;` always sits before them. The three `+= 0xA` were one
increment the delay-slot filler copied into each jump's slot. Written as the author did, the loop is
`for (i = 0; i < D; i++) { draw(0xA0, i * 10 + 0x50); ...; switch (i) { case 0: ...; case 1: ...; case 2: ...; } }`:
60 -> 0 (the app's judge, `fast_judge.py`). The same function from m2c's raw draft (220) reaches 0 too. Three of the
four children decomprepair `induction_reverse_reduction` offers are 0 on it, including the one that only deletes the
three increments and writes the uses as `i * 10 + 0x50`, keeping the `do` loop and the gotos: the reduced variable is
the whole difference, the `for` and the `switch` are spelling.

Failed experiments that would otherwise be repeated:
`prove_chain.py --families all` from the 60 stalls: 1218 children, none better; `--combine 10 --children 400
--lookahead 3` finds a combined step (`guard_temp_sink+m2c_refinement`, still 60) and nothing after it. The
existing `stepped_induction_index` needs the steps as the last statements of a `do` body and a start nothing else
touches; here the steps sit inside a goto chain, and the loop is behind a guard. No existing family offers the
reduction, the folded increments or the chain as a `switch`.

What is controlled evidence and what is one function:
One function (the draft, and the draft with its guard flag); the library's tests are synthetic. The module counts a
path's steps by running the chain on each counter value that matters, so it does not offer the reduction for a chain
that steps a variable twice or never on some path.
