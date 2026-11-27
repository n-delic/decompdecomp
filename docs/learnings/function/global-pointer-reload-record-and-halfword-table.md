---
title: A read-only global pointer reloaded after a store, and a halfword table m2c wrote as char
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: an N64 effect update of 71 instructions
(SN gcc 2.8.1, flags above). Saved best 500: m2c's draft with the callee's argument added. Two shapes differed. The
target loads a global pointer again after `sh` at offset 2 of the object, before reading a word through it, and the
candidate kept the first load. The target stores `sh $zero` into a table indexed by a byte times 2, and the draft's
`extern char T[]; *((i * 2) + T) = 0;` gave `sb`.

The source change, and the measured result before and after (scores, the judge used): isolated judge, then the app.
- `extern void *G;` as `extern struct { void *block; } G;`, every read `G.block`: 500 -> 240.
- `extern u16 T[]; T[i] = 0;`: 500 -> 300 alone, 240 -> 40 after the first edit.
- The two counter updates at the entry in the other order (`x34 += 1` before the store of `x3C`): 40 -> 0.
decomp-repair `global_pointer_read_record`, `scaled_byte_table_element` and the existing `store_run_order` reach 0 from
the saved best in those three steps. The match relies on no scaffolding beyond the one-member record around the global.

Failed experiments that would otherwise be repeated: earlier attempts (notes) left "the target reloads the global" open
at 300 with plain `extern void *G` and respellings of the reads.

What is controlled evidence and what is one function: one function. That the reload comes from the store at a constant
offset being a structure member (which does not invalidate a plain scalar's load) while the store at offset 0 is not is
a hypothesis read from expr.c's `MEM_IN_STRUCT_P` marking, not traced through cse.c.
