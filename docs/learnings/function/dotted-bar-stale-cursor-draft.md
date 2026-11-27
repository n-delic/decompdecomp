---
title: A display-list draft with a stale cursor global, and the loop edits that finish it
kind: function
compiler: sn-n64-281 (SN GCC 2.8.1), -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 114-instruction N64 routine that draws two rows of small fill rectangles (a fixed count of 20, then a count passed
in), with a few set-up commands before each row. Profile in the header above. The autopilot's saved m2c draft scored
3895; it named the display-list cursor global where the register still held an older value of the cursor, advanced it
with `+=`, and typed the accesses as records.

The source change, and the measured result before and after (scores, the judge used):

Written by hand as `Gfx *_g = gfx++` blocks, `for` loops, and `x`/`y` copies of the two coordinate parameters set
before each loop with the counter, it matched on the third variant. From the saved draft, by named steps (the app's
judge):

- the commands as the SDK macros, reading the stale cursor uses as the run's first read, `G_FILLRECT` decoded: 565;
- plus a copy of the other coordinate parameter beside the copy of the stepped one: 225;
- plus one pair of locals and one counter for both loops: 60;
- plus the second loop's starting values below the commands before it and the guarded `do`/`while` as a `for`: 0.

decomp-repair `gbi_stale_cursor` does the first step and `gbi_stale_cursor_loops` the other three in one child.

Failed experiments that would otherwise be repeated:

- `y = arg1` copies with `x = arg0` set before the counter: 690 (the counter is set first in the first row).
- At 60, moving the starting values below the commands alone, or writing the `for` alone, each stayed at 60.

What is controlled evidence and what is one function:

One function. Why the parameter copy and the shared locals change the registers was not read from the compiler
source (hypothesis: they are longer-lived pseudos that global allocation ranks differently).
