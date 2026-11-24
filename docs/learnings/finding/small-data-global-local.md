---
title: small data global local
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-24
---

What the function (or case) looked like and the exact compiler and flags: a 30-statement object constructor (call, nine
field stores into the returned object, one more call) built by SN GCC 2.8.1 with `-G8 -mips2 -O2`. It copies one 4-byte
global into a field. In the target the global's `lui` is hoisted above an unrelated store and the `lw` comes three
instructions later. The m2c draft declares `extern s16 G;` and reads it `*(s32 *)&G` (or `extern s32 G;` read `(s32) G`).

The rule: a global declared as a sized scalar of at most `-G` bytes is small data. GCC reads it through one assembler
macro (`lw $r, G` = `lui; lw`), so the `lui` is never an instruction of its own and every scalar spelling (cast read,
`volatile`, a pointer local) leaves it next to its load. A global that is not small data (an incomplete array,
`extern s32 G[];`, read `G[0]`) is split by the compiler into `%hi`/`%lo`, and the halves are scheduled. Alone that
scored worse (410 where the scalar was 80: both halves sank to the use); the read held in a once-set local, set right
after an earlier store and stored later into the field, was the one of eight positions that scored 0.

Measured with the fast judge (the app's own, read-only): a reconstruction of the hand-matched function 1045 -> 0 with
the local placed after the first store (the positions before it 760-815, 295, at the use 540); a second `-G8`
function's saved best 130 -> 0 with the array alone (a pointer global read through `M2C_FIELD`); the seeds of two
matched `-G8` functions 110 -> 0 and 800 -> 290.

Failed experiments that would otherwise be repeated: the array without the local (410); the local without the array
(scalar read held in a local: the macro still fuses the `lui`); a local above a call (not offered: a call may change
the global).

What is controlled evidence and what is one function: three functions of one game, all under this profile (`-G0`
profiles split every global, so the family is silent there). Offered by the decomp-repair family
`small_data_global_local`.
