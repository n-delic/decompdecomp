---
title: A value built up before two calls was written at each read
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 102-instruction N64 object update (profile above, a per-function profile). A raw random value is turned into a speed
that scales the results of two calls. The target does `andi; srl; addiu` *after* the first call returns, in front of its
`mult`, and keeps the result in `$s0` for the second product. m2c's draft built it in a local ahead of both calls
(`t = (u32) (raw & 0xFFFF) >> 6; t += 0x733;` ... `f(a) * t` ... `g(a) * t`), which computes it before the first call.

The source change, and the measured result before and after (scores, the judge used):

The app's judge, from the saved best at 590:

1. The byte counter's search-made `&global` pointer local written as the global (`symbol_base_unwrap`): 590 -> 300.
2. The speed written whole at both reads, `f(a) * (((u32) (raw & 0xFFFF) >> 6) + 0x733)`
   (decomp-repair `accumulated_temporary_inline`, new): 300 -> 120, three reordered rows left.
3. The raw halfword store at offset 2 written as a struct member (`inferred_record_layout`), which lets the byte
   counter's load move above it: 120 -> 0.

Each alone: step 1 300, step 2 410, step 3 530. `prove_chain.py` over those families reaches 0 from the saved best with
9 judged variants. A typed-struct rewrite from the target's assembly (`obj->frame = f(obj) + rand() % 4`,
`++counter == 2` then `counter--`, a `u16` raw value) also matched on its first compile.

Failed experiments that would otherwise be repeated:

Without step 2, every existing family together stopped at 180: `single_use_inline` declines a local that `+=` assigns
again. About 150 children of the 590 state were judged before the cap.

What is controlled evidence and what is one function:

One function. That the left-to-right evaluation of the product puts the arithmetic after the call is a hypothesis the
measurement agrees with; it was not read in the compiler's source.
