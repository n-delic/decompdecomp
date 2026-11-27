---
title: a call argument read from a global, computed above the store before its call
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 42-instruction message line drawer: it copies a three-byte colour record to the stack, stores a zero byte into a
record array indexed by a global, then calls a string drawer with that record's text and a y position of the same
global times 15 plus a constant. The target loads the global once before the store (the `<< 4` is shared by the
index's `* 17` and the argument's `* 15`) and loads it again after it for the text pointer.

The source change, with the app's judge: the argument written inside the call scored 1325; a local holding the
global and used as index and in the argument scored 175. `y = line * 15 + 0x3C;` assigned above the store, with the
global itself everywhere else, scored 0. The record copy is one struct assignment; m2c wrote three byte copies.
decomp-repair `call_argument_above_store` and `derived_local_reread` write both forms. `global_byte_run_struct` now
also fires on the draft, where a pointer copy of another global followed the three bytes (draft 1920 -> 1195).

Failed experiments: reusing the local in place before the call (`line = line * 15 + 0x3C;`, 20: only the first two
`lui` swapped, with the declarations in either order); the new local declared before the record copy (555).

What is controlled evidence and what is one function: all of this is one function. That the second load comes from
the byte store's possible alias is a hypothesis; it was not read in the compiler source.
