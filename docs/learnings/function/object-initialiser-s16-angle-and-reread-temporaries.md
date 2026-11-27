---
title: object initialiser s16 angle and reread temporaries
kind: function
compiler: SN64 GCC (sn-n64-281) -G0 -mips3 -O2 -mgas -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 111-instruction N64 object initialiser (two random calls, a byte-colour set, a table lookup, an early return on a
global flag, then a random velocity from `sine` and `cosine` of a random angle), SN64 GCC `-G0 -mips3 -O2 -mgas
-mhard-float -mfp64`. The saved best was the untouched machine draft at 1850; judged 0 after these edits:

- `(x & 0xFFFF) % 3` divided unsigned (`multu 0xAAAAAAAB`): `(u32)` on the operand or `3U` (`unsigned_constant_divide`).
- Each arm's `speed = ((u32) (x & 0xFFFF) >> 8) + 0x266;` removed and written, cast to the temporary's type, at both
  multiplies, so it is computed after the `sine` call (`reread_temporary_inline`, decomp-repair).
- The object's raw offset stores written as a struct's fields: the load of the global flag then moves above the store
  of the halfword into its delay-slot place (`inferred_record_layout`).
- `u32 angle` read only as `(s16) angle` declared `s16` and read bare (`cast_reads_narrow`, decomp-repair): the
  shift after the second call then comes before the colour stores (80 -> 0).

Measured chain from the saved best, in that order: 1850 -> 820 -> 340 -> 220 -> 0. Each of the four edits also lowered
the saved best's score on its own (820, 1370, 1730, 1630), and leaving any one out of all four did not match.

Failed: `angle = next(); angle >>= 3;` and a chained `a = b = c = 0x80` stayed at 80; `angle = (s16) angle;` before
the arms was 180.

One function; that the `s16` local's machine mode is what moves the shift in the first scheduler is a hypothesis.
