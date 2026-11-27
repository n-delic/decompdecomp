---
title: particle list output cursors as counters
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: an 87-instruction loop over 500 particle
records on an N64 game (SN GCC 2.8.1, flags above). Live records are updated by a switch, then appended to two output
arrays: an 8-byte position record per particle and three colour bytes. The target keeps the two output pointers in saved
registers set up after the particle pointer's `%hi` copy, and loads a second global's `lui` inside the loop.

The source change, and the measured result before and after (scores, the judge used): hand-written pointers
(`pos->x = ..; pos++;`, `*colour++ = r;`) scored 1929 (saved best 2880): the second global's `lui` was hoisted into a
saved register and one position field got a derived pointer. Writing both as the arrays indexed by counters
(`positions[n].x = ..; n++;`, `colours[c++] = r;`) was exact (0) in the app, with no profile change. Each cursor alone is
840 and 1232 in the isolated judge. decomp-repair `array_cursor_counter` writes it.

Failed experiments that would otherwise be repeated: `-fno-strength-reduce` brought the pointer form to 1126, and adding
`-msoft-float` to that to 240 with only prologue order left; both were the wrong lead (neither is the game's build). Six
statement orders of the initialisers, `do`/`while` loops and `inline` helpers did not close it.

What is controlled evidence and what is one function: one function. That loop.c's instruction count decides the hoist was
read from the `-dL` dumps and `loop.c` (the movable threshold); why the counter form changes it is a hypothesis.
