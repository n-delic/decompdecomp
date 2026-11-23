---
title: repeated block per axis scratch reuse
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like and the exact compiler and flags: a 163-instruction float function, one basic block, that
sets up a cubic's forward differences for three axes from four input points (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32
-O2`). The saved best (8465) had nine per-axis coefficient locals, nine derived-value locals stored at the end, copies
of the inputs, a scratch reused for half-expressions, a `volatile` output pointer, a union around two inputs and a
`frame[10]` pad.

The source change, and the measured result before and after (isolated judge `fast_judge.py`, then the app's
`compile_and_diff`): the exact source is three scratch variables `a`, `b`, `c` assigned again in each axis block
(`a = (...) * h3; b = (...) * h2; c = (...) * h;`) with the derived values written straight into the output where they
are computed (`out->dx = a + b + c; out->dddx = a * 6.0f; out->ddx = a * 6.0f + b * 2.0f;`), the four inputs read as
`p[i]`, `q[i]`, `r[i]`, `s[i]` and the output a plain `const`-input record. 8465 -> 3846 (merged roles, derived values
stored in place) -> 4 (the derived values kept in locals and stored at the end: only two spill slots swapped) -> 0
(derived values with no locals). Why: `a` is one multi-set pseudo, so global.c gives it the same callee-saved float
register (`$f28`, `b` `$f30`, `c` `$f26`) in all three blocks and the scheduler cannot move a block across its
neighbour (the anti-dependence); nine independent pseudos interleave the blocks. The derived-value locals add pseudos the
target does not have, and the spill slots are given in pseudo-number order (a declared local's number is lower than a
temp's), so with locals the `2b` temp and `ddx` swapped slots (`0xc`/`0x14`, score 4).

Failed experiments that would otherwise be repeated: about 27 000 automatic trials and 100 hand attempts on the nine
named locals never reached it (each edit alone is neutral or worse). A search of 69 single-assignment temporaries in all
valid emission orders (simulated annealing on the order inversions of the final instruction stream, then on the score)
got nowhere near: statement order does not matter once the roles are shared (every order of `a`/`b`/`c` and of the
stores scored the same), the DAG decides. `r[i] + r[i]` against `r[i] * 2.0f`, `b + b` against `b * 2.0f`, the copies
`x = p[0]` and a union around `q` are neutral in the final form; `volatile` on the output (+1040), `frame[10]` (+31),
`b` before `a` in all three blocks (12706) and `c = h * (...)` (5) are not; non-`const` input pointers were 15328.

What is controlled evidence and what is one function: one function. The repair is reproducible from a draft with the
library's `repeated_block_scratch_reuse` (merge plus direct stores as one child, 14959 -> 0), `repeated_block_order_swap`,
`immediate_scratch_inline` and `volatile_output_parameter_plain` (decomp-repair PR 1430).
