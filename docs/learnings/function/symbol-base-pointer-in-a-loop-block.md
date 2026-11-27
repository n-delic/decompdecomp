---
title: A byte member bumped through a symbol base the target holds in a saved register
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281 -G0 -mips3 -O2 -mgas -mfp64)
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 91-instruction N64 spawner (SN GCC 2.8.1, flags
above) that spawns one object in one arm and four in a loop in the other, bumping a byte at a symbol plus 2 per object.
The target builds the symbol's address once before the loop (`lui $v0; addiu $s3,$v0,%lo(sym)`, then `lbu 2($s3)`);
m2c's `M2C_FIELD(&sym, u8 *, 2)` folds the offset into `%lo(sym+2)`.

The source change, and the measured result before and after (scores, the judge used): from the m2c draft (app judge),
three single families, each an improvement: `symbol_address_local` (a pointer set inside the loop's `if`, just before
the use) 1030 -> 330, `u16_counter_loop` (the `& 0xFFFF` do-while as a `u16` for loop) 330 -> 60,
`store_adjacent_gather` (x, y, kind store order) 60 -> 0. No scaffolding. `prove_chain --families <the three>` reaches
0 from the draft; `--families all` with a small `--max-judged` stops first, since the first batch that improves hides
the larger step.

Failed experiments that would otherwise be repeated: a pointer local assigned before the loop puts its `lui/addiu`
ahead of the counter's `move` and the loop's constant (595 on a hand-written version); assigned inside the loop body,
loop.c hoists it after them.

Search bug found on the way: for a symbol first named in an `else if` condition, `symbol_address_local` set the
pointer between the `}` and the `else`, so the child did not compile (fixed in decomp-repair pull request 2109).

What is controlled evidence and what is one function: one function.
