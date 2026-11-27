---
title: Table insertion with countdown copy, stray seeding parameter, two-arm return, base-local word reads
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

An 81-instruction N64 function: an outer loop finds a place in a ten-entry table, an inner loop shifts the entries (a word
array and an array of 4-byte name records) down, a call writes each name, and the place plus one is returned.

Measured on the app's judge. A matched sibling in the same file (the same insertion for two players) gave an exact source
on the first try: `u16` loop counters in `for` loops, a `u32` table, `char names[][4]`, a `u8` place, `shift = 99` as
"not found", `if (shift < 10) return (u8)(place + 1); return 0;`.

From the saved best (3432, m2c and search output) the steps that matter, each needed (removing one left 40 to 1962):

1. The masked shadow of the countdown (`copy = n & 0xFFFF`, with `n -= 1` hoisted above the body's reads) becomes `n`,
   with the decrement at the loop's end.
2. The word table declared as bytes is read as words. It was read through a local holding its address, and inlining that
   local alone scored no better while the loads were bytes.
3. The parameter that only seeded the place goes (`(void)`), and the place is a byte or halfword (a word was 415 on the
   exact source).
4. `if (n >= 10) v = 0; else v = (place + 1) & 0xFF; return v;` becomes two returns with the test inverted.

decomp-repair `countdown_copy_merge`, `stray_parameter_seed_drop`, `two_arm_result_return` and
`asm_byte_table_deref_width` (now seeing the base local), with `symbol_base_unwrap`/`symbol_base_inline`, chain
3432 -> 1782 -> 932 -> 340 -> 45 -> 5 -> 0 (34 variants).

One function: the families are controlled by their own tests, the chain is this one case.
