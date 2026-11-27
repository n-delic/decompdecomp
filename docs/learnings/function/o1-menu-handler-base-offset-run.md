---
title: o1 menu handler base offset run
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-27
---

A 137-instruction N64 menu state handler, `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (a matched neighbour using the
same globals had that profile; the function sat under the default -O2).

Plain C (record array, `switch` on a halfword state, a `u8` changed flag): 5640 at -O2, 991 at -O1. In one block only,
the target reaches a byte as `-0x2c($s0)` off the neighbouring array whose address `$s0` holds; later blocks use the
byte's own name. Writing that block's test and decrement as `((u8 *) other)[-0x2C]` gave 10; declaring the `u8` flag
(initialised to 0) before the `s32` flags (initialised to 0) gave 0 (the app judged it exact).

`neighbour_symbol_rebase` offered one reference or all; it now offers runs of two or three (991 -> 10), and
`initializer_placement` does the last step (10 -> 0), both measured with `prove_chain`.

Failed: `volatile` on the byte (7460), `-fforce-addr` at -O2 (6025).

Verified for this function; one case.
