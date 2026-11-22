---
title: A 219-instruction N64 sequencer function: eight edits from 335 to 0
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-22
---

A track-advance loop (219 instructions, a 64-iteration loop over sequencer tracks with a rewind path, an instrument
setup block and a table copy), `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2`. Saved best 335, all rows register
choice or order. One function, judged by the app's own judge (fast_judge, then compile_and_diff: byte-identical).

Edits, each score is the combined state after the edit (the 14 rows left at 230 were all register rows):

| edit | score |
|---|---|
| start 335 | 335 |
| drop the shared `x * 4` variable; write it at its use in the load address | 360 (a1 now the lhu temp) |
| source written as `(a1 << 2)` plus the pointers in the order the target adds them (`off + (mem4 + base)`) | 275 |
| the rewind block's `lhu` and the last block's sum are one variable (`temp_a2_3`, global); the sum's other reads inline | 255 |
| int sums `(u32)f + (u32)base` where m2c's `ptr + int` puts the pointer first | 245 |
| the shared sum `gSeqPlayer + b` computed in each arm (both arms start with the same insn: reorg's redundant-insn delay slot) | 230 |
| `(u32)M + (u32)T + s1` for the pointer sum of the first copy | 220 |
| temp written into the store reading it three times (destination address first) | 160 |
| `q = mem + base; ... (a1 << 2) + q` for the header | 155 |
| last block: `t >>= 16` on the shared sum (S ties nothing; it is the same register) | 140 |
| `k = 0xFF` set between the counters, compared in the loop | 30 -> 0 with `var_s2`/`var_s5` swapped |

Compiler facts read from the source (local-alloc.c, cse.c) and the cc1 dumps: c-typeck's pointer arithmetic puts the
pointer first whatever the source order, so `(u32)` casts are what choose an `addu`'s operand order; local-alloc ties a
sum with its first dying operand only when that operand is local to the block (a variable used across blocks never
ties, which is why a variable shared by two blocks keeps a register to itself); loop.c's hoisted `li` sits just before
the loop, after every plain assignment, so a constant written as its own local among the assignments moves where the
prologue has it. Hypotheses: why the quantity of the header's offset ranks above the base sum's (a tie at equal priority,
broken by quantity number).

Permuter gaps found: the chain from 255 with all existing families stopped at 255 (prove_chain, 78 children, none better).
decomprepair 0.5.631 adds `entry_constant_local` (140 -> 30; the existing `preheader_run_order` then reaches 0) and
`store_temp_inline` (220 -> 160 on its own). Still missing: `integer_pointer_sum` for sums with a dereferenced operand
and nested in an address; the arms' shared sum sunk into both arms; one variable shared by two blocks' temporaries
(rewind lhu and last-block sum).

Failed: strength-reducing the counters (`var_s1 * 0x10`, `* 0x28`) in place of the explicit counters (1458-2065, the
givs were never reduced under this loop); swapping the sum's source operand order for ptr + int (no effect: the front end
puts the pointer first); a fresh variable for the last block's sum (250, var_a0 moves to a2).

Controlled: the scores are one function's. The two families are controlled only by that function and synthetic tests.
