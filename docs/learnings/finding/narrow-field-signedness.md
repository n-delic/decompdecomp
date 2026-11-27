---
title: a narrow field's signedness shows in the constant stored into it and in the load that reads it
kind: finding
compiler: sn-n64-272-0001 -O1
evidence: verified
date: 2026-11-27
---

What the functions looked like: two N64 functions (micromachines64turbo, SN GCC 2.7.2 build 0001, -O1) one opcode off.
One set a flag byte with `M2C_FIELD(temp_v0, s8 *, 0x17) = 0xC8;` where the target loads `0xC8` (`addiu $r, $zero, 0xC8`);
the other compared a halfword with 1 as `*(u16 *)(D_802083F0 + D_800AD1A8 * 0x270) == 1` where the target has `lh`.

Measured by compiling both spellings with `cc1n64.exe -G8 -mips2 -O1`: `s8` field = 0xC8 gives `li -56`, `u8` gives
`li 0xc8`; `s16` = 0xC800 gives `li -14336`, `u16` gives `li 0xc800`; a compare of a `u16` read gives `lhu`, of an `s16`
read `lh` (an ordering compare `sltu` against `slt`; bytes `lbu`/`lb`).

The source change: `M2C_FIELD(temp_v0, s8 *, 0x17) = 0xC8;` -> `u8 *`, and `*(u16 *)(...) == 1` -> `*(s16 *)(...) == 1`.
Both matched (score to 0 in the app). Library: decomp-repair `constant_store_unsigned` (constants 0x80..0xFF into
s8, 0x8000..0xFFFF into s16: M2C_FIELD stores, `*(sN *)` stores, assigned members; the twin of
`member_immediate_signedness`) and `compare_load_sign` (reads in a compare changed to the sign of the target's loads of
that width), v0.6.251, tests cover each width and both directions.

Failed experiments that would otherwise be repeated: `field_width` did not fire on the store: it needs the offset's
accesses in the target to name one type, and a store only counts when no load of that offset exists.

What is controlled evidence and what is one function: the compiler output of the four stores/loads is controlled
(compiled directly); the two matches are one function each.
