---
title: An object reset from its draft - a phantom argument, a found-node walk and literal goto paths
kind: function
compiler: SN GCC (sn-n64-281), -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 259-instruction N64 object reset: a player-indexed `else if` ladder, a two-player position ladder, a dispatch to one
of four calls returning a byte, then a spawn of three linked objects or a walk of the existing list. SN GCC
(`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`, the function's own profile).

The source change, and the measured result before and after (scores, the judge used):

From the machine draft (5565 in the app's judge) to 0, in order:

1. m2c's second parameter was `$a1` read before set, the register of the local that holds the calls' results; the
   callers pass one argument. The local replaces it and the goto join becomes an `else if` chain: 4400.
2. The search loop with the found node handled in the other arm, written as a `while` that returns at the list's
   end: 3840.
3. The jumps into other arms' tails written out, the literals they carried written in their stores, the `!=` nest as an
   `else if` chain: 2090. Written out alone it scored 4575, with literals 3960.
4. Branch polarity, the table's element types, one cursor local, two parent stores after the child's counter byte, a
   byte clear after the table store: existing families, down to 60.
5. The dispatch reads the field in each test instead of a `u8` local (which kept an extra `andi $v1, $v1, 0xff`).

Steps 1, 2, 3 and 5 are decomp-repair `m2c_argument_register_local`, `goto_found_walk`, `goto_paths_literals` and
`condition_read_reread` (now on `M2C_FIELD` reads). A rewrite from the target's assembly with a typed record was exact
within a handful of compiles (1385, 420, 300, 0).

Failed experiments that would otherwise be repeated:

The found-node walk with `break` and an `if (p != NULL)` after it scored 630 where `return` scored 420. Chaining only
goto families from step 2 (19 children) improved nothing: the written-out paths pay only with the literals and the
chain together.

What is controlled evidence and what is one function:

One function. The scores are the judge's on the app's own target; the compiler reasons for each step were not checked in
the compiler's source.
