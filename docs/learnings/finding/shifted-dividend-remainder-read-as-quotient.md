---
title: m2c reads (x >> k) % n as (x >> k) - (x / (n << k)) * n
kind: finding
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2
evidence: observation
date: 2026-11-27
---

A 94-instruction N64 object update (SN64 GCC 2.8.1, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`) whose
machine draft scored 705 with these rows: the `mult` one instruction out of place and `sra` by 2 and 3 where the target
shifts the high word by 1 and 2. The draft had `(t >> 1) - ((t / 10) * 5)` and `(u >> 1) - (((c + 1) / 14) * 7)` with
`u = c + 1`. GCC takes the sign correction of `(x >> k) / n` from `x` (`x >> 31`, as the target's `sra` of the unshifted
register shows), which m2c reads as `x / (n << k)`.

Writing `(t >> 1) % 5` and `(u >> 1) % 7` was exact (705 -> 0, the app's judge). Each site alone scored 355 and 350.
The draft is not the target's program: for negative values `x / 10` and `(x >> 1) / 5` differ.

decomp-repair `shifted_dividend_remainder` writes every such site as one child, then each alone.

Controlled: the library's unit tests of the shapes (the dividend repeated or held in a local, the quotient and product in
locals, refusals). One function: the scores above.
