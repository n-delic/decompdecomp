---
title: A global reached as M2C_FIELD(&D_x, ...) wants one record declaration
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

What the case looked like: a 22-instruction N64 leaf that initialises a buffer-region record (a global with start, end,
size and three state bytes), `sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The m2c draft
declared it `extern M2C_UNK D_x;` and reached each field as `M2C_FIELD(&D_x, T *, K)`. Judged 985: the target forms the
address once, stores the field at offset 0 through the symbol (`sw $a0, %lo(D_x)($a2)`) and the others through the
address register, while the casts gave stores at negative offsets from a later field's address.

The source change: one record type from the offsets and types, `extern Rec D_x;`, each access `D_x.mK`. Nothing else
changed (the draft's temporaries and statement order stayed). Judged 985 -> 0 in the app's judge. A hand version with
named fields and read-backs instead of the draft's temporaries was exact too.

Permuter: decomp-repair `m2c_field_record_view` read only plain pointer names as bases; PR RetroReversing/decomp-repair
#2282 adds `&global` bases, and its one child of the saved draft is exact. A 30-variant chain of the other global
families (`global_offset_symbol`, `field_struct_view`, `global_read_record`, `unsized_extern_array`) reached 730.

What is controlled evidence and what is one function: the unit tests cover the rewrite and its refusals; the score is
one function.
