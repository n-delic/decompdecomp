---
title: actor update entry argument and pointer record
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 51-instruction actor update: counters, a halfword clamp, a state byte read through a global record pointer, byte and
word stores through that pointer, and a closing call that releases the actor. `sn-n64-281 -G0 -mips3 -O2 -mgas -meb
-mcpu=VR4300 -mhard-float -mfp64`.

The autopilot best (575) held the global as `extern void *G; base = &G; (*base)`, passed a loaded byte and `base` to
the closing call, and carried search scaffolding. A typed rewrite reached 405; declaring the global
`extern struct { T *p; } G;` then gave 0 (app judge). The target reloads the pointer after each word store through it.

From the saved best, one step at a time (isolated judge): `callee_arity_trim` 575 -> 565, `scaffold_strip` -> 365,
`entry_argument_restore` (new) -> 210, `global_pointer_base_record` (new) -> 50, `clean_rewrite` -> 0.

- The target never writes `$a0` before the call, so the argument is the function's own first parameter.
- `global_pointer_read_record` already gave 405 -> 0 on the typed rewrite; on the draft it never fired, because the
  address local reads as the global's address being taken.

Failed: plain `*(s32 *)((u8 *)G + K) = 0` and `(u32)G + K` stores on the typed rewrite stayed at 405 (no reload).

One function; the scores are measured, the reason the record member forces the reload (`MEM_IN_STRUCT_P`) is the
library module's hypothesis and was not traced here.
