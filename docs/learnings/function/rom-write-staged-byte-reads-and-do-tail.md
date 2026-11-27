---
title: A ROM write with staged byte reads and a do-loop tail copy
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-27
---

A 58-instruction memcpy-like routine that writes RAM bytes to cartridge space: a byte loop to word alignment, a word
loop that packs four source bytes into one argument, and a byte tail. Profile
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`; scores are the app's judge.

The saved best (250) was an m2c draft with search scaffolding. Two edits made it exact, each improving alone:

1. The byte tail, `a = dst; do { ...; a = dst; } while (n);`, written `do { a = dst; ... } while (n);`
   (250 -> 150). Family `loop_top_cursor_copy`, which until now took only `for (;;)` loops.
2. In the word loop, m2c read three of the four bytes into temporaries, stepped the second pointer, then passed the
   last temporary again as a third argument the callee does not take. Writing the reads into the packing expression,
   the step after the call and the stray argument gone gives 250 -> 100. New family `call_load_temps_inline`.
   Reordering the three temporaries alone gave 120, not 0.

Both together: 0. The matched source keeps the m2c shape otherwise (a `goto` word loop with a stepped second pointer, a
`goto` alignment loop, parameter copies, `do { } while (0)` wrappers).

A readable rewrite is also exact: three `while` loops that use the parameters themselves, the bytes written as
`src[0] .. src[3]`. What failed on the way to it, from the m2c draft: the word loop as a real loop while the alignment
loop stayed a `goto` (595 and worse), and real loops with the parameters still copied into locals (740-745). A matched
neighbour with the same alignment, word and tail loops was the model.

One function; the two rewrites are also covered by controlled tests in decomp-repair.
