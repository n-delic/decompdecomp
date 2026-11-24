---
title: a push onto a global array at a global count wants the store through the old count and the array's pointer first
kind: finding
compiler: SN64 GCC 2.7.2 (sn-n64-281), -G0 -mips2 -O2 -fno-schedule-insns
evidence: verified
date: 2026-11-24
---

What the function looked like: a 15-instruction N64 function that pushes a value onto a bounded stack kept in a global
byte array, indexed by a global 32-bit count (`array[count] = v; count = count + 1; if (count + 1 >= LIMIT) count = LIMIT - 1;`).
m2c writes it as `P = array + count * 4; Q = count + 1; count = Q; *P = v; if (Q >= LIMIT) count = LIMIT - 1;`, and
the saved best held the array in a byte pointer local first (`bp = array; P = bp - (-(count * 4));`). That stood at 15
(three register rows: the base in `$v0` instead of `$a1`, the address sum's operand order).

The source change, and the measured result before and after (scores, the project judge): two coupled edits, neither
alone enough:
1. the store written through the OLD count, ahead of the count's own store, and the test recomputing `slot + 1` instead
   of holding the sum in a temporary (`slot = count; table[slot] = v; count = slot + 1; if (slot + 1 >= LIMIT)`):
   60 alone (base in `$a1` and the address sum index-first, but the count's `lui` ahead of the array's);
2. the array's pointer held in a local (`table = (s32 *) array;`) assigned as the FIRST statement, before the global
   count's load: 60 -> 0.
Library: decomp-repair `counter_array_push` (v0.5.1009) offers both together (the combined child) and each alone.
`prove_chain --families all` from the saved start: 15 -> 0 in one step; from the m2c draft (210): 0 in one step (before
it the chain stopped at 10 from the saved start, and reached 0 from the draft only through a do-while scaffold).

Failed experiments that would otherwise be repeated (all 60 or worse): the base assigned after the count's load; the
count's store before the indexed store (240); the increment in a local of its own (60 / 255 / 360); `count++` folded
into the index; index-first address sums and `&((s32 *) base)[count]` (65). The compiler-side reason (the order the
two `lui`s are first seen in) is a hypothesis; only the judge decided.

What is controlled evidence and what is one function: one function, verified by the project judge (0), with the
single-edit controls above. False-positive load of the family: it fired on 1 of 2,964 matched/best sources and 1,650
drafts of one 2,206-function project (the function it came from), so it is cheap to keep in the draft path.
