---
title: pointer walk index both ways
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: controlled
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

Loops over an array of records written by m2c as a counter and a pointer that steps a constant stride each pass (`n += 1; p += 0x18;`, accesses `M2C_FIELD(p, s32 *, 4)`), in N64 functions compiled with SN GCC 2.8.1 (`-mips3 -O2`, `-G0` and `-G8`). The compiler keeps the walking pointer as a register of its own, set before the loop, and the constants it hoists take other registers. The game's indexed loop (`for (i = 0; i < n; i++) base[i].f`) makes ONE stepping pointer set after the hoisted constants.

Four functions (a particle update, a menu draw, a per-record scan, an object-list scan) were decided by writing the walk as the counter indexing the base. `pointer_walk_to_index` removes the pointer step and writes every access `M2C_FIELD(p, T *, n * K + off)` (a record pointer stepped by one: `p[n].f`); a use after one of the two steps and before the other counts the difference (`(n - 1) * K`), or the counter's step moves to the end of the body. A matched function with two walks reverted: 6310 -> 1140 -> 0 (one walk per step).

Eight other functions needed the reverse: the target steps one cursor beside the counter (`e = table; for (i = 0; i < n; i++, e++) e->f`), where the indexed `table[i].f` scored 670 to 4055. `index_to_pointer_walk` reverts them: from the indexed form of eight matched functions, 670 -> 0, 950 -> 0, 970 -> 0, 980 -> 0, 1455 -> 0, 1605 -> 0, 2580 -> 0, 4055 -> 0.

The source change, and the measured result before and after (scores, the judge used):

See above (the app's own judge on the project's profile, read-only).

Failed experiments that would otherwise be repeated:

The m2c walk with a byte stride (`p += 0x18`) on a `void *` pointer needs the offset spelled with the counter (`n * 0x18 + off`); a typed record pointer stepped by one indexes the type. Not covered: a cursor chain `for (c = find(K, NULL); c; c = find(K, c))` that m2c writes as a `goto` loop, and a pointer walk with no counter (`for (; p != end; p++)`), which needs a bound to count to.

What is controlled evidence and what is one function:

Controlled: 8 + 1 matched functions reverted and recovered. Which of the two forms the original used is not readable from the C, so both directions are offered and the judge decides.
