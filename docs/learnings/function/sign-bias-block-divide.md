---
title: sign bias block divide
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1 -g
evidence: verified
date: 2026-11-27
---

A 150-instruction N64 object update function (KMC GCC 2.7.2, `-O1 -g`) passed three values to a callee, each the object's
signed word divided by 256. m2c wrote the first two as its hand-written sign-bias blocks and the third already as a
division:

    var_a1 = (*(s32 *)((s8 *)arg0 + 0));
    if (var_a1 < 0) { var_a1 += 0xFF; }
    var_a2 = (*(s32 *)((s8 *)arg0 + 4));
    if (var_a2 < 0) { var_a2 += 0xFF; }
    var_a3 = (*(s32 *)((s8 *)arg0 + 8));
    f(&G, var_a1 >> 8, var_a2 >> 8, var_a3 / 0x100);

Scored 260 with the app's judge. Writing each load with its own division and dropping the blocks and shifts

    var_a1 = (*(s32 *)((s8 *)arg0 + 0)) / 0x100;
    var_a2 = (*(s32 *)((s8 *)arg0 + 4)) / 0x100;
    var_a3 = (*(s32 *)((s8 *)arg0 + 8)) / 0x100;
    f(&G, var_a1, var_a2, var_a3);

scored 0. The order is part of the repair: loading all three first and dividing in the call (`var_a1 / 0x100, ...`)
scored 380, because the compiler schedules each division next to its load only when the division is in the load's
statement.

The decomp-repair family `sign_bias_block_divide` (0.6.253) makes both spellings as children for any k (`2^k - 1` added,
`>> k` later, V the shift's whole left operand and every later reference). `prove_chain.py --families
sign_bias_block_divide` reached 0 from the 260 source in one step (6 children scored); it is a different shape from
`sign_bias_shift_divide` (the `(x + ((u32) x >> 31)) >> k` expression) and from `signed_division` (the block directly
before the line that shifts).

One function; the 380 result is the same source with the division at the use.
