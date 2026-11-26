---
title: O1 table lookup and interpolation, index written back into its argument, float parameters reused
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: two 63-instruction N64 float functions of a
project whose default is `-O2` but where several hundred functions already have a per-function `-O1`. Both matched only
under `-O1` (applied per function from a `compiler_experiments` trial).

The source change, and the measured result before and after (scores, the judge used):

1. A clamped table lookup (`andi $v1,$a1,0xffff; sll/addu; sll $a1,$v0,1; andi $v0,$a1,0xffff; lw $v1,...`).
   `i = (u16) idx; idx = i * 6; off = (u16) idx;` (the scaled index written back into the argument), then
   `off = (u16) idx; base = <load>; p = off * 4 + base;` in that order. 25 -> 0. From the machine draft the app's chain is
   `reload_temp_inline`, `temporary_hoist`, `scaled_carrier_writeback` (250 -> 0, decomp-repair 0.6.120).
2. An interpolation with eight float arguments. Differences and reciprocals written back into the parameters
   (`hi = hi - lo; hi = 1.0f / hi;`), the constant return written at its first site with the later arms jumping to it,
   and two `do { } while (0)` weights (around `arg5 = arg5 - arg1;` once and `arg3 = arg3 - arg1;` three deep) with the
   last difference split (`den = A; arg3 -= ...; arg3 *= ...; den -= arg3;`). From the machine draft, 2655 -> 0 as one
   `param_difference_reuse` child (with `shared_return_hoist` and `shared_return_split`); from a hand rewrite without the
   weights, 125 -> 0 by `local_weight_ladder`.

Failed experiments that would otherwise be repeated: on the interpolation, a hand rewrite that held `arg3`'s priority
below the other float parameters stayed at 55-75 through some hundreds of spellings of the last block. `greg` showed
`arg3` (7-8 refs, 34 live) allocated after `arg5`, `arg6` and `arg7` where the target had it first in `$f2`; only the
weights moved it. On the lookup, one local for the masked index and the product, or the pointer sum written
base-first, left 15-25 points of register rows.

What is controlled evidence and what is one function: each match is verified by the app's judge; the allocation
readings are hypotheses from one function each. The weights are scaffolding for allocation priority, not claimed to be
the original source.
