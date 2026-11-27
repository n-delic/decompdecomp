---
title: A loader that byte swaps a table entry's words, written as the swap macro's steps
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 155-instruction N64 loader: it DMAs a directory from the cartridge once, finds an entry, byte swaps the entry's size
and offset words, rounds the size up to 16 and DMAs and unpacks the data. The m2c draft scored 4613 (4298 refined).

Measured with the app's judge, from the refined draft; each was needed (dropping it from the exact set scores shown):

- The swap macro's spelling, the shift written at both uses and the last mask kept, not m2c's `t = x >> 0x10; ... (t >> 8)`,
  which GCC folds to `x >> 24` (1160).
- The swapped word kept in its own variable and rounded up in place as `<< 4`, before the other word is swapped (2555;
  `* 0x10` there: 600).
- The other word swapped in place, then added to the base (475).
- The ROM base address in a local set at the top and advanced (1685).
- `buffer = GLOBAL; buffer = round(buffer);` as two statements (745), the same for the destination address (400).
- One buffer local for both rounded globals (2253), set before the entry's words are read (20).
- The early `return 0` (305).

decomprepair `endian_word_load` makes all of these; its `endian_word_load_early` child is exact from either draft, and
the word-level `endian_word_load` child alone scores 2453. From there `disjoint_local_merge` reached only 1445.

Failed or slower ways: a matched sibling loader (86% similar code) adapted by hand started at 3135 until the return
shape and the buffer reuse were also changed.

Controlled evidence is this one function; why GCC wants each form is a hypothesis, not read from its source.
