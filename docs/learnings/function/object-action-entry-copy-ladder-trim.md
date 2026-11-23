---
title: object action entry copy ladder trim
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

A 94-instruction object-action function (two integer parameters, a function-pointer call through a table, a call with
eleven arguments, a two-way tail), m2c saved best 755, exact (0) after five edits. Standalone judge and the app's
`compile_and_diff` agree. This is one function; each mechanism below is a hypothesis from the dumps (`passes.py`,
`cc1 -dl -dg -dS -dR`) and the compiler's `sched.c`, not a controlled rule.

Edits, with the score after each (judge scores in parentheses are for the step alone from the previous source):

1. m2c's tail `*(&D_x + temp_s0)` on an `s32` symbol scales the byte offset again; the target reads
   `lhu` at `D_x + id * 16`. Written `*(u16 *)((u8 *)&D_x + temp_s0)`: 1066 (library: `table_read_width`).
2. `local_weight_ladder` on the table read: 220.
3. `temp_s0 = arg1;` after the declarations and the first two `arg1` masks read as `temp_s0`, while the late
   `(arg1 | 0x7000)` keeps the parameter: 155 (`reused_local_entry_copy`).
4. The other parameter overwritten after its entry copy (`arg2_in` clobber): 80 with 3. Alone it did nothing until 3
   existed. The first scheduler skips the leading `hard reg -> pseudo` parameter copies (sched.c `schedule_block`,
   confirmed in source) and orders the rest by priority then position, so the target's `move $s3,$a2` after
   `andi $a1,$s0,0xff` needs the copy to be a statement of the body (`entry_parameter_pair`).
5. The call's `do { } while (0)` written as a backward `goto` that never runs (`loop_goto_form`) together with the
   ladder around `var_s7`'s load removed (`ladder_trim`): 135 and 145 alone, 0 together (`ladder_trim_goto`).

The scaffolding the match relies on: the dead `arg2_in = 0;` store, the `do { } while (0)` ladders around the table
reads and tail calls, and the `if (0) goto` that holds the call. All stay faithful programs (nothing stored to a wrong
address, no phantom locals); polishing removes what it can.

Failed: narrowing `arg1` to `s16`/`u16` (the conversion insn does end the first scheduler's pinned head, but it makes
the later `& 0xFFFF` a real sign or zero extension: 240 to 560); widening the 4th callee formal to `s32` (identical);
local copies of `arg2` that cse folds away; `s16`/`u16` on `var_s7`/`var_s4` (815 to 1400).

Controlled: from the saved best, `prove_chain.py --families all --combine 10` with decomp-repair after this change
reaches 0 (755 -> 595 -> 260 -> 60 -> 0). One function.
