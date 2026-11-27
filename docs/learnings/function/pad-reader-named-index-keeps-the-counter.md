---
title: a named `i * 2` keeps the loop counter loop.c would otherwise trade for an end pointer
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: verified
date: 2026-11-27
---

A 77-instruction N64 pad reader: two stick axes into a two-entry stack array, each turned into two held-direction bytes
of a global array and two bytes of a four-byte stack array, which the function packs into its result.

## What decided it

With the products written inline (`out[i * 2]`, `out[i * 2 + 1]`, `held[i * 2]`, ...) the typed rewrite scored 965:
loop.c reduced the addresses, eliminated the counter and rewrote the exit test as `sltu` against an end address (the
`.loop` dump says `biv 80 was eliminated`). The target keeps `$t1` as the counter with `sltiu $v0, $t1, 2`.

```c
for (i = 0; i < 2; i++) {
    s32 j = i * 2;
    if (abs(stick[i]) < 20) {
        out[j] = out[j + 1] = 0;
        ...
```

scored 0 (the app's judge). The dump says `giv of insn 71 not worth while, 0 vs 55`: the local is a derived value loop.c does not reduce,
it still reads the counter, so the counter stays (`biv 80 can be eliminated`, but it is not).

`abs()` was needed (the same test written as a conditional expression: 925). Chained or separate stores of the pairs
were both exact.

## From m2c's cursor draft

The saved best (2470 under this profile) was m2c's frame union and five lockstep cursors. Measured on the way:

| step | score |
|---|---|
| frame union as arrays, search scaffolding removed | 2500 |
| `counter_companion` (cursors written off the counter) | 1515 |
| `loop_scaled_index_shared` | 820 |
| `walk_counter_for`, then `field_width` (`prove_chain --families all`, stopped at its cap) | 320 |
| by hand: typed pad-pointer table, no pointer locals for the table or one element, store order, `&&` | 300 |
| `do_while_to_for` (init moved into the header across the two reads) | 0 |

The `do`/`while` form of the exact source scored 300: its one differing branch was `blez` where the target has `blezl`.

## What failed

Hand-made cursor sources with a second counter `j += 2` beside `i` reached 525 and no lower: the preheader order and two
registers stayed wrong. `u32`/`s32` for the counter, `i < 2U` and a `u8` stick array with casts changed nothing.

The families are decomp-repair's `loop_scaled_index_shared` (new), `counter_companion` and `do_while_to_for` (both
extended). The draft's last steps from 320 are still by hand.
