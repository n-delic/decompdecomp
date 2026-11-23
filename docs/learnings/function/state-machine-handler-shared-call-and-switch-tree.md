---
title: state machine handler shared call and switch tree
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 970-instruction N64 state-machine handler (a `switch` on a state halfword whose arms pick the next state with one
shared call, a second `switch` tree on the next state, a probability roll, two float-compare sound triggers), KMC GCC
2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`. The saved best was m2c's output with a few search edits (6745, about 105
register rows, 177 differing rows). Not matched: 230 recorded (6745 -> 230, 6 differing rows left in one group).

The source change, and the measured result before and after (scores, the judge used; the app's own judge, standalone):

Each row is one edit, its score alone on the previous state, and the decomp-repair family that now offers it.

| edit | score | family |
| --- | --- | --- |
| `var_s3_2` (typed `char`, set from `flag & mask`) merged into the `long` flag `var_s3` | 6745 -> 6335 (register rows 105 -> 21) | `disjoint_local_merge` (a `long` local now pairs with a narrower one, #1479) |
| `call(..); *(p + 0xA4) = 0;` in one arm swapped to store-then-call, so the arms' tails match | 6335 -> 6155 | `call_store_swap` (#1481) |
| the calls written in each arm, so jump2 cross-jumps them into one shared `jal` | 6155 -> 4310 | `call_into_each_arm` (existing) |
| `(rand(100) + 1) <= 0x21U` -> `rand(100) < 0x21U` | 4310 -> 4060 | `compare_bias_fold` (#1492) |
| `((x >> 2) & 7) < 2` through `(s16)`: `slti`, not `sltiu` | 4060 -> 3860 | `masked_compare_signed_cast` (#1482) |
| `do { do { call; break; } while (0); } while (0);` unwrapped, so the `break` leaves the switch (m2c's `goto loop_goto_end` written `break`) | 3860 -> 3390 | `fence_break_unwrap` (#1493) |
| `scratch.sp44` read from a local (`lw $a0,0x44($sp)` once, not per use) | 3390 -> 3120 | `member_read_local` (#1494, lint #1495) |
| a reused `temp_v1_9` split into a byte local; a halfword local; the read of a field left until after the call that precedes it | 3120 -> 2520 | `wide_local_to_halfword`, `reload_temp_inline`, `split_integer_lifetime` (existing; they reached 1905 -> 1170 from the edited source) |
| the compare tree written as `switch (state) { case 0: case 2: case 4: case 8: ... }` instead of m2c's `if` tree with `goto` into an `else` and an identity helper around `x == 4` | 1170 -> 770 | `label_block_switch` (#1497) |
| `inline_fn*` identity helpers on a record address dropped | 770 -> 750 | none (the helper is scaffolding; `scaffold_strip` covers some) |
| the sound-trigger call's address argument (`p + 0x24`) staged in a local before the one-statement `if` that sets its last argument: the branch-likely over `move $a2,$zero` becomes the target's `bc1t` with the argument in its slot | 750 -> 230 (490 per arm) | `call_arg_stage_before_guard` (#1503) |

Two of the app's judge scores moved by constants during the run because other agents renamed a global and a callee
(`gTrackMarkerTable`, a distance helper): a private candidate keeps the old names, the judge prints `?name` for them in
its rows, and the score grows by 150 per use. Rename in the candidate before reading any score.

Failed experiments that would otherwise be repeated:

- Five spellings of a call's third argument (`(char *) p + 0x84`, `&((char *) p)[0x84]`, a `do { } while (0)` around the
  call, `(void *) ((int) p + 0x84)`) compiled identically: the cause was the identity helper `inline_fn(p)` used by
  the earlier stores and loads of that record (the address `p + 0x84` was kept in a register from the first use);
  writing each use as `((char *) p) + 0x84` moved the argument's `addiu` into the delay slot (770 -> 750).
- `a || b` written plainly (`x == 4 || x == 8`) compiles to `xori; sltiu; xori; sltiu; or; beqz` (value arithmetic);
  the identity helper made the first test `xori; beqz`; only the `switch` gives `li 4; beq` / `li 8; bne`.
- A merged flag typed `char`/`s8` (one variable): the byte read gets an extension (`sll; sra`) the target does not have.
  The target has two pseudos (a word flag, a byte flag copied from `v0`), and 1190 with them separate.
- `var_a2 = 1; if (!(a > b)) var_a2 = 0;` written as an `else` arm, a conditional expression, or two calls (one per
  value): 1170, 1170, 1890.

What is controlled evidence and what is one function:

Every row above is one function (an observation); the table's scores were measured one edit at a time on the state
before it, with the app's standalone judge, and each new family reproduces its row from that state. The chain
`prove_chain` found from the saved best with the existing families alone stopped at 5030 (`call_into_each_arm` three
times): the register, store-order, compare and fence steps were not offered.

Not closed (230, one group, stated as the compiler behaviour with no known source shape):

- A byte flag whose pseudo (4 references, 11 live, priority 0.73) is allocated before the object pointer (76
  references, priority 0.58) and takes `$s2`; the target gives the pointer `$s2` and both flags `$s3` (`and $v0,$s3,$v0;
  move $s3,$v0`, `sltu $v0,$zero,$v0; move $s3,$v0`). With the flag merged into the word flag the order is right and the
  two copies are gone (6 rows, 230); with two pseudos the copies are right and the registers swapped in 94 rows (670).
  Fences around single statements (46) or blocks (150), a hard-register binding (not accepted by the judge) and every
  narrow type for the second flag leave 670; the chain over every family (1,700 children a step) reached 550 only by reusing the word flag as scratch for an unrelated product (`product_operand_reuse`), which leaves the same 98 register rows and is scaffolding.
