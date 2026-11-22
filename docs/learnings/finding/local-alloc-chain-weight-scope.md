---
title: a flag chain outranks a neighbouring index chain in local allocation with one-shot scopes, and a row loop's commuted statement
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -fno-strength-reduce
evidence: observation
date: 2026-11-22
---

What the function looked like: a 167-instruction mesh-frame decoder (an N64 game, `gcc-2.7.2-kmc -G0 -mips3 -mgp32
-mfp32 -O2 -fno-strength-reduce`, the function's own profile). The saved best (35, seven register rows) had two groups
left:

1. The frame-validity flag (`lh limit; sltu; negu; and frame,frame,mask`) in `$v1` while the shape-table index
   (`andi; sll; addu`) took `$v0`; the target has the flag chain in `$v0` and the index in `$v1`. Both chains are local
   to one basic block, and the saved best reused the flag's variable for the ROM offset a few statements later.
2. The third vertex coordinate of a loop body: `mul.s $f0,$f0,$f2` and `lwc1 $f2` where the target has
   `mul.s $f2,$f2,$f0` and `lwc1 $f0`. The saved best wrote the loop as a `__repair_rows:` goto block with
   `z = off[2] + (scale[2] * p[2]);` beside `c[0] = (p[0] * scale[0]) + off[0];`.

The source change and the measured result (app judge worker, then `compile_and_diff`; every row an edit from the saved
best, score alone and combined):

| edit | alone | with the others |
| --- | --- | --- |
| E1: the ROM offset in its own local (`romAddr`), the flag's variable no longer reused | 45 | with E2: 10 |
| E2: `do { flag = frame < limit; shape = rec->shape; } while (0);` and `do { frame &= -flag; } while (0);` | 35 | with E1: 10 |
| E3: the row loop as `do/while`, its third statement `(p[2] * scale[2]) + off[2]` like its siblings | 25 (the `do/while` alone 50, the sibling order alone 60) | with E1+E2: 0 |

E2's two scopes are only useful together (the definition's scope alone 35, the reader's alone 80, the scope around the
compare alone 60 or 145 depending on the neighbour): the scope is also a scheduling barrier (sched.c attaches the loop
note to the next insn, which then depends on every insn before it), so a scope around the compare alone moved it after
the table lookup, and the two loads must be inside the first scope to keep their order. The match is exact (compile_and_diff).

Why (read from the 2.7.2 source, the numbers not instrumented in the compiler, so a hypothesis): local-alloc.c ranks a
block's quantities by `floor_log2(refs) * refs * size / (death - birth)` and hands each the lowest free register, and
flow.c counts a reference once per loop the insn sits in, so a `do { } while (0)` is one more reference at no instruction
cost. Merged, the index chain (4 references over 2 insns) outranked the flag chain (6 over 7) at depth 1; with the two
scopes the flag chain's references are doubled where they sit in a scope and it ranks first. A variable reused later
(`reg_n_deaths != 1`) is not a local quantity at all, which is why the split (E1) is part of it.

Failed experiments that would otherwise be repeated: declaring the index as a named local, a second set of the index, the
statement order of the five statements (all 120 orders: best 45), `frame &= -flag` before or after the lookup, other
spellings of the compare, wrapping every statement or the whole group (145-340), and making the index a global pseudo by
using it after the branch (the index then took `$a1`).

What is controlled evidence and what is one function: one function, one compiler build. The library families
`chain_weight_scope` (E1+E2 as one child) and `goto_loop_sibling_shape` (E3 as one child) reproduce the chain from the
saved best: 35 -> 25 -> 0.
