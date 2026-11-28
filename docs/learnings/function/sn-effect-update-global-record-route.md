---
title: An effect update that reloads a global pointer matches by existing families from the machine draft
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: an N64 effect update of 32 instructions
(flags above). It ages the object, advances a phase, writes three colour bytes and copies a word and two halfwords
through a global pointer, which the target loads again before each of the three copies. Saved best 360: the m2c draft
with `extern void *G;`, one load too few.

The source change, and the measured result before and after (scores, the judge used): from the saved draft,
`prove_chain.py` reaches 0 in two steps with the app's isolated judge: `global_pointer_read_record` (360 -> 40), then
`store_run_order` (40 -> 0). `reloaded_global` (a volatile pointer) also scores 40 at the first step and is picked
first when both are offered. By hand the same record (`extern struct { void *p; } G;`) with the timer's statement
before the phase's matched, as recorded by the app.

Failed experiments that would otherwise be repeated: typed struct pointers for the object and the target with a plain
global pointer (1030: the pointer is loaded once); casts with a plain global pointer (320); passing the object to the
release call while the global is plain (675: the object is moved out of its argument register). With the record,
passing it costs nothing.

What is controlled evidence and what is one function: one function. The families that cover it were already in the
library; nothing new was added. See also `global-pointer-reload-record-and-halfword-table.md`.
