---
title: a record spawner's late loaded store and one local for two spawned records
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 59-instruction N64 effect spawner: it reads a serial-number global, fills the parent record, spawns a child
(`c = alloc(); if (c != NULL) {..}`), fills it with constants and `parent->serial + 0x21`, then spawns two more.

The source change from the machine draft (saved best, app's judge, 850), in order:

1. One local for the serial global, read above the first field store. The draft's stores are m2c's expanded spelling
   `(*(T *)((char *)(p) + N)) = v;`, which `global_read_local` did not count as pointer stores (850 -> 240).
2. The loaded value's store (`child->serial = parent->serial + 0x21;`) moved above seven constant stores of the child
   (240 -> 45). In a hand-written struct form the same move alone was 295 -> 0; six places up was 60 and five 330.
3. The second spawn's result written into the first spawn's local (45 -> 0).

Families: `global_read_local`, `late_load_store_hoist`, `call_result_one_local` (called in turn on the saved best,
each child scored by the standalone judge; the chain ends at 0).

Failed experiments: the draft's `register` and casts did not matter. `statement_slide` reaches four places and only
`M2C_FIELD` or record-cast cells, and `member_store_hoist` wants a store passing member reads, so neither offered
the move (0 of 640 children of the 295 form held it).

One function; the compiler's reason is a hypothesis (the load's register living across the constants changes which
registers the constants and the record pointer get), not read from the compiler source.
