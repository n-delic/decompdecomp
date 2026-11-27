---
title: stored member readback before the limit load
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 34-instruction N64 particle update (SN GCC 2.8.1) stores a halfword sum and then compares it against a limit loaded
from the same record. The saved best was 60: `sh` came before the limit's `lw`, where the target has `lw`, `sh`,
`andi`.

The change, from `*(s16 *)(p + 0x6A) = sum; if (limit < (sum & 0xFFFF))`:

- the compare reads the stored member as unsigned, `if (limit < *(u16 *)(p + 0x6A))`; the compiler still has the value
  in a register, so there is no load and the `andi` stays;
- the search's `do { } while (0)` around the sum is opened.

Measured with the app's judge: read-back alone 60, fence opened alone 640, both together 0 (accepted by the app).
Either side of the compare may hold the member (`a < m` and `m > a` both 0). The stored local may be `u16`, `s16` or
`s32`. A chain over every earlier library family (202 children) found no improvement.

decomp-repair `stored_member_readback` (as written) and `stored_member_readback_unfenced` (the combination, exact from
the saved best).

One function: this is not a rule about the scheduler. That the read of the member is what holds the store below the
limit's load is a hypothesis.
