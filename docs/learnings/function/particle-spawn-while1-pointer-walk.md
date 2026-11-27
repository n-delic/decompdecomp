---
title: A particle spawn loop: while(1), pointer stepped first, squares in place
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 214-instruction collision handler spawning
ten particles into the free slots of a record array (flags above); a 201-instruction sibling reuses the same loop.

The source change, and the measured result before and after (scores, the judge used): a record pointer `p` walked in a
`while (1)` loop with `if (i >= 10) goto done;` (the compiler derives the second field pointer itself), the free-slot
search as `p++; j++; if (j >= 501) { i = 501; break; }` with the pointer first (1300 -> 480), the saved position in two
`u16` locals instead of a two-halfword struct (480 -> 35), and `dx *= dx; dy *= dy;` before the distance call
(35 -> 0, family `square_in_place`). The app's judge. The sibling matched at 0 with the loop copied.

Failed experiments that would otherwise be repeated: a hand-written second pointer (`q = (u8 *) p + 0x14`) stayed at
945 or worse; `for` and plain `while (i < 10)` forms rotate the loop (2760 and more).

What is controlled evidence and what is one function: two functions of one game.

Open: a 196-instruction sibling handler is at 400. The target keeps a second `if (hit != NULL)` test (a `beqz` on a
path where the compiler could know the pointer is null); `||`, `&&` with return, `goto` and `do { } while (0)` fences
all fold it to a `j`.
