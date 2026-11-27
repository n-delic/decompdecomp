---
title: A jump table whose entries share one address is a copy of the body per case, and the stores after it go through a block pointer
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 55-instruction N64 page setup (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`): a switch
whose jump table has six entries that all name one block, then three word stores into one record. The draft stacked
`case 1:` to `case 6:` over one body, which compiles to a range check, no table (best 1965).

Measured with the app's judge:

1. Each case label with its own copy of the body (`case_label_copies`): the table appears and cross-jumping leaves one
   body, 800. 1,101 automatic trials had not recorded this.
2. The three `M2C_FIELD(&D, s32 *, 0x14/0x18/0x1C) = 0;` after the switch through `s8 *p = (s8 *) &D;` set in a block of
   their own (`symbol_run_pointer`): 0. The target holds `D` in a register (`sw 0x14($v0)`); ours folded the first
   offset into the address.

Failed (800, no change) for step 2: `((s32 *) &D)[5]`, a one-member struct cast per store, `M2C_FIELD` redefined as a
member access, and no other library family on the 800 source (77 children). A struct view with the three words as
named members or as an array member was also exact.

Controlled: the neighbouring 90-instruction function (two bodies in its table) went 2655 -> 800 -> 0 with the same two
steps in the isolated judge. Why GCC keeps the base only through the pointer or the record is not traced (hypothesis:
cse treats a user pointer's offsets apart from `symbol + constant`).
