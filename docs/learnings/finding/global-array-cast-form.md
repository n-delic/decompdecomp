---
title: global array cast form
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: controlled
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A small N64 object update or a draw routine (SN GCC 2.8.1, `-G8 -mips2/-mips3 -O2`) read a global pointer or integer that the draft declared as a plain scalar: `extern Obj *G;` read `G->f`, `extern void *G;` read through m2c's field macro, or `extern s32 G;`. Under `-G8` such a global (4 bytes) is small data and one assembler macro loads it, so its `lui` can never be scheduled apart from the load. The targets had `lui %hi(G)` in a register of its own, placed above unrelated stores, with `%lo(G)` loads through it (a load into a different register, or several accesses sharing one `lui`).

Declared `extern u8 G[];` and read `(*(Obj **) G)->f` (a pointer) or `(*(s32 *) G)` (an integer), the compiler materialises the address as an ordinary constant and the `lui` moves to the target's place. The plain array `extern Obj *G[];` read `G[0]->f` also splits the `lui` but not the same way: through the byte array's type the compiler cannot prove the loads distinct from the record the pointer points at, so stores to the record's fields go by without forcing the pointer to be loaded again and the field loads can move above those stores.

Measured with the app's judge on the project's own `-G8` profile, the matched source's cast form reverted to the plain global and `global_array_cast_form` run on it: 2487 -> 0, 2119 -> 0, 935 -> 0, 952 -> 0, 1365 -> 0, 10 -> 0 on six functions; 1295 -> 100 on a seventh (the array form alone; the rest of that match was another change).

The source change, and the measured result before and after (scores, the judge used):

See above (the app's own judge on the project's profile, read-only).

Failed experiments that would otherwise be repeated:

`extern T G[];` read `G[0]` (the array of its own type) was the winner for three integer scalars and the cast form for the pointer globals; the other form scored 100 to 900 worse in most cases, so both are offered, the cast form first for a pointer and the array first for an integer. A global read in one macro (the `lui` loaded into the very register that receives the load, nothing between) is not split in the target, and the retype made it worse.

What is controlled evidence and what is one function:

Controlled: six matched functions reverted and recovered by the family. The explanation of why the cast form lets the loads move is read off the targets, not from the compiler's source. The gate (`lui` into a register other than `$at`, a load into another register or a shared high half) and the `-G` size come from the flags and assembly, so a profile with `-G0` never fires.
