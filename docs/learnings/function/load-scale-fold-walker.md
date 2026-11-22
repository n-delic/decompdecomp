---
title: a byte-coded record walker (switch in a loop) matched by folding the scale into the load, one u8 selector and no wrapper
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

An 87-instruction N64 function (KMC gcc 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`) that walks a byte-coded stream: a `switch`
on the byte, each arm adds a count byte to one total, stores a kind (0, 1, 3, 2) and skips a record of `count * 3 + 1`,
`* 6 + 1`, `+ 1` or `* 2 + 1` bytes (the 1 and 6 arms share the `*2` tail); the loop ends on byte 12. Saved best 130,
which read an uninitialised local in an identical-arm guard, kept the byte in three word locals and wrapped the last cases
in `do { case ...; } while (0)` with a padding local.

Edits from the saved best, measured with the app's exact judge (130 -> 0, no scaffolding left):

| edit | alone | combined |
| --- | --- | --- |
| collapse the identical-arm guard | 285 | |
| merge raw, masked copy and tail reload into one `u8 opcode` | needs the collapse | 70 (with the collapse) |
| `m = *p; ...; m *= 3;` -> `m = *p * 3;` in the two arms that scale | 100 (130 -> 100); one arm alone: no change | 40 |
| take off the `do { } while (0)` wrapper around the last cases (and its padding local) | 180 (worse) | 0 |

Compiler fact (hypothesis from the allocation tables, not read in the source): `m = *p; m *= 3;` loads straight into
`m`, so `m` is a join of loads; `m = *p * 3;` loads into a pseudo of its own (the multiply's operand) and `m` is only the
product. The join local then takes `$v0` as in the target and the loaded byte `$v1` (the target's `lbu $v1,0($t0); sll
$v0,$v1,1; addu $v0,$v0,$v1`). The arms without a multiply keep `m = *p` (`lbu $v0,0($t0)`). The selector as one `u8`
gives the target's `andi $v1,$a0,0xff` before the entry compare and the unmasked `bne $a0,$t1` at the tail with the
`andi` in its delay slot.

Library families (decomp-repair PR #1251, each declares itself): `load_scale_fold`, `byte_selector_merge`,
`switch_tail_scope_unwrap`; `prove_chain --families` of the three from the saved best reproduces 130 -> 70 -> 40 -> 0.

Failed experiments: a clean rewrite with `long m; m = *p; m *= 3;` (50, ten register rows); `m` typed `u8` (1875);
`m = *p * 3` in one arm only (50 and 30); `p += *p * 3 + 1` with no local (1730, the arms stop sharing the `+ 1` tail);
`phantom_wide_local` on the clean 50 (30, scaffolding: not used).

Controlled evidence: the table above is one function. The three rewrites are tested on a synthetic shape, not on other games.
