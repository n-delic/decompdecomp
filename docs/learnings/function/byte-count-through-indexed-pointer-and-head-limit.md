---
title: a loop count read a byte through a loaded pointer plus an index, and a limit computed at the head
kind: function
compiler: KMC GCC 2.7.2 -O1 -g (-G0 -mips3 -mgp32 -mfp32)
evidence: verified
date: 2026-11-27
---

What the function looked like: a 150-instruction N64 object search: four range limits `(s32)(G - H) >> 7`, a table
pointer loaded from a record, and a `do`-style loop over the entries counted by a byte read from a second table
(`*(s32 *)(*(s32 *)(rec + 0x14) + i)` in the saved C), the fourth limit computed inside the `if (count > 0)` guard. 595.

The source change, and the measured result: (1) the count read as `*(u8 *)(...)` (the target's `lbu $s1,($v0)`): 595 -> 395;
(2) the fourth limit assigned beside the other three at the function's head: 595 -> 200 alone; both: 0 (the app's
judge, `fast_judge.py`). Cleaning the `for (;;)`/`goto` ladder into `do { } while (count > 0)` with the return flipped to
`if (D_800B84E0 != 0) return;` changed no byte (395 stayed 395, and 0 stayed 0), it is only polish. Families:
`loaded_pointee_width` now follows an `addu` of an index onto the loaded pointer (it required the narrow load to read
straight from the loaded register), and the new `guarded_assignment_head_hoist` moves a pure global-read temporary out of
an `if` block to its siblings. `prove_chain --families loaded_pointee_width,guarded_assignment_head_hoist`: 595 -> 200 -> 0.
The earlier `--families all` run (400 judged) found no improving child at all.

Failed experiments that would otherwise be repeated: none besides the all-family chain above.

Controlled evidence and what is one function: one function; the hoist family is a scheduling/numbering hypothesis (loads
ordered by statement order, pseudos numbered by first set), the judge decides.
