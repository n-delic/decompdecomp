---
title: a void draft whose target ends with move $v0,$sN returns the local in $sN
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-24
---

Two small N64 functions (44 and 62 instructions) allocate an object (`temp_v0 = alloc(...)`, kept in `$s0`), fill it in,
and end with a store through it. The target's tail is `lw $v1, 0x70($s1)`, `move $v0, $s0`, `sw $v1, 0x70($v0)`: the
last store is addressed through `$v0` because the function returns the object. m2c's draft is `void` (no caller reads
`$v0`) and stores through `$s0`: one missing `move` plus two register rows, score 110.

Source change: `void` becomes `void *` and `return temp_v0;` is added as the last statement. 110 -> 0 on both, judged
with the app's own judge (fast_judge, read only). Copying the local into a `dst` local before the last store (four
spellings) stayed 110.

Why no family reached it: `void_parameter_return` needs `$v0` copied from an argument register;
`returned_register_local` declines `void` drafts. `prove_chain --families all --children 400 --steps 12 --combine 10
--lookahead 3` stalled at 110. New family `void_returned_local` (decomp-repair 0.5.1027) reads the epilogue copy of a
register that holds a call result (or the prologue's parameter copy), picks the local (named after the register, else a
pointer local assigned from a call and stored through), and offers `T *f` / `s32 f` with the return after the last
store. Proven from the 110 start: `prove_chain --families void_returned_local` reaches 0 on both. It fires on 29 open
saved bests of one 2,200-function game (counted, not judged).

Evidence level: two functions, one compiler; the bytes cannot tell the pointer return type from `s32`.
