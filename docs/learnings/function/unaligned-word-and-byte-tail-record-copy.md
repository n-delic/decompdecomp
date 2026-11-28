---
title: unaligned word and byte tail record copy
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 48-instruction N64 asset-loading routine
(`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`) that starts two locals from constants, a
table of six pointers and six byte counts. The target copies the counts with `lwl`/`lwr`, two `lb`, `swl`/`swr` and two
`sb`; m2c writes `temp = M2C_UNALIGNED32(...); sp28 = temp; sp2C = ...; sp2D = ...;` and passes `temp` as an extra
argument.

The source change, and the measured result before and after (scores, the judge used): isolated judge
(`fast_judge.py`, `prove_chain.py`), from the saved machine draft at 3840: `unaligned_tail_struct_copy` (decomprepair
0.6.469: the counts as one byte-record assignment) 2335, `symbol_stack_block_copy` 725, `nested_guard` 210,
`callee_arity_trim` 0. The app recorded a hand source with the same shapes as matched (0). Without the new family the
other three stopped at 2216. Hand form: two record assignments (`PsdNames names = D_...;`) and nested `if`s.

Failed experiments that would otherwise be repeated: `__builtin_memcpy` of six bytes from a symbol declared as a word,
with or without a `(char *)` cast, copies with `lw`/`lh` (1200): the symbol's declared alignment wins, so the source
has to be read as a byte record. `x < 7 && x > 0` on a global folds to one `sltiu` (515); the target's `slti` and
`blez` need nested `if`s.

What is controlled evidence and what is one function: one function. The alignment result is two compiles of it.
