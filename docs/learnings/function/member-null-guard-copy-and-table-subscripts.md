---
title: member null guard copy and table subscripts
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 142-instruction N64 effect update: a hit arm and a movement arm that each null-test a linked object and copy colour
bytes from a three-byte-per-entry table into it. Saved m2c best 2470 after 45 attempts; the app's judge.

A typed rewrite from the assembly was exact on its second try. Reduced to edits of the saved best (each needed):

1. `if (a && b || c && d || ...) state = 3;` as an `if` / `else if` chain (`or_arm_split`): 2470 -> 1780.
2. A halfword stored with `0xFFFF` typed `u16` (`ori`, not `addiu -1`; `field_width`): 1445.
3. A table byte the draft read as a word, at the target's width (`asm_address_read_width`): 1300.
4. `*(u8 *)(i + T)` and `M2C_FIELD((i + T), u8 *, k)` as `T[i]` and `T[i + k]` with `u8 T[]` (`table_sum_subscript`,
   new): 1045.
5. `v = obj->m; obj->x = K; if (v)` as `obj->x = K; if (obj->m) { v = obj->m;`, one local `v` for both arms
   (`guard_member_copy`, new): 655. The target has `lw $v0; beqz $v0; ...; addu $a1,$v0,$zero`.
6. The index local `u16`, then its masked word copies written as itself (`wide_local_to_halfword`,
   `masked_copy_inline`): neutral, then 0.

What did not work: restoring the two guard copies with a local per arm was no gain (380 -> 380); the shared local was.

Evidence: one function. The two new repairs reproduce steps 4 and 5 from the saved best through the judge.
