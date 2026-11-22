---
title: a clamped halfword result kept in one narrow local
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-22
---

A 94-instruction N64 menu function: a byte read from a record, moved by a signed step, clamped between a halfword and
the halfword above it (both packed in one word), then stored, tested and passed to a formatter. m2c's draft kept the
value in two locals, a wide `s32` carrier and an `s16` the compare read, copied one into the other before and after
the clamp. Saved best 125 (one row missing, `move $s1,$v0`, and four register rows).

Source change, judged with the app's exact judge (125 -> 0):

1. One `s16 x` for the whole value: `x = *(u8 *)p; x = x + step; if (...) x = hi; else if (...) x = lo;`. The wide local
   goes. Alone: 220. Target shape: `addu $v0,$s1,$s4; move $s1,$v0` then `sll $v0,$v0,16; sra $v1,$v0,16` (a halfword
   assignment from a word is a plain move with no extension, and the extension at the compare reads the sum's register;
   hypothesis from the `-dr` dump, not from the compiler source).
2. The compare `if (lo_masked < x)` written `if (x > (lo & 0xFFFF))`, with the mask local that held `lo & 0xFFFF`
   written into it. The target extends the signed halfword first and masks the unsigned one after it; gcc evaluates the
   left operand first. Alone: 220.

Together 0. decomp-repair `narrow_result_unify` (families `narrow_result_unify`, `compare_sides_locals`,
`compare_sides_inline`, `narrow_result_unify_compare`) offers each and the combination as one child; prove_chain found
nothing on the saved best before it.

Failed on the way: a separate `sum` local with `x = sum` (cse reads the extension from the lower-numbered copy and
combine folds the move away: 320); `u32` for the low bound (the copy from the loaded word is then folded: 250); a
`cur`/`sum` pair with `cur = sum` (320).

Controlled evidence: the combined child reproduces 0 from the saved best. One function; the mechanism is a hypothesis.
