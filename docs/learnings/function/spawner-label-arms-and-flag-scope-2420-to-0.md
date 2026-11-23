---
title: spawner label arms and flag scope 2420 to 0
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 713-instruction N64 object spawner (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, sixteen parameters, a 51-case
switch whose arms set a kind byte and a flag word, a second switch of 43 table entries that picks a sound and a state).
Saved best 2420 (an m2c draft that agents had reshaped for weeks: shared `var_v1`/`kind_mask`/`var_v0_N` locals and
`goto set_kind_flags`/`setup_path_kind` labels inside the switch, a `volatile short` callee, `volatile` pins). 16,000+
automatic trials had not moved it; of the 1,374 children every library family offered on the saved best, none of the 1,358 scored beat 2420.

The source change, and the measured result before and after (scores, the isolated judge, confirmed by `compile_and_diff`):

Eight edits, each one a library family now (decomp-repair, `prove_chain` from the saved best reaches 0 with them):

| step | edit | alone | in the chain | family |
|---|---|---|---|---|
| 1 | callee `volatile short randomIntBelow();` -> `s32`, and `3 < (temp_a0 + 1)` -> `temp_a0 >= 3` | 2520 / 2470 (worse) | 2420 -> 2270 | `narrow_return_compare` (one child with both) |
| 2 | the second switch's `case 0: case 1: case 45:`..`case 50:` aliases of `default:` removed (the target's check is `addiu -2; sltiu 0x2b`) | 2465 (worse) | 2270 -> 2015 | `switch_range_trim` |
| 3 | `*((volatile s32 *) (p + 0x5C)) |= K` written plain | 2560 (worse) | 2015 -> 1855 | `volatile_cast_unpin` |
| 4 | every `goto set_kind_flags`/`block_75`/`block_76`/`setup_path_kind` arm carries its own copy of the run, with `v`, `mask`, `kind`, `flag` block-local, literals folded, `t = v & M; X = t \| K` as `v &= M; v \|= K; X = v`; the arm's earlier statements between the `\|=` and the store; word updates written `X = (X & M) \| K` get the same form | 865 / 140 | 1855 -> 140 | `switch_label_arms`, `switch_label_arms_mixed` |
| 5 | `do { arg15_flag = arg15 << 0x18; } while (0); if (arg15_flag != 0)` | 25 better on the draft | 140 -> 0 | `parameter_test_scope` (offered with the arms as `switch_label_arms_scoped`) |

`prove_chain` order from the saved best: steps 1, 2, 3, then the one child that has steps 4 and 5 together (1855 -> 0).
The family modules declare themselves in the decomp-repair registry; the app needs no wiring.

Why each was invisible:

- **Step 1 and 5 are allocation, not source shape.** `explain_regs.py` named the first register pair (the sixteenth
  parameter's `$s3` where the target has `$s2`) and the rule from `global.c`: saved registers go in order of
  `floor_log2(refs) * refs / live` (an `int` after `* 10000`), each to the lowest free register. The fourth parameter
  (`arg3`: five references over 120 instructions, 0.083) outranked the sixteenth (a flag test, two over 28, 0.071), the
  target has them the other way. A `do { } while (0)` is a loop for `flow.c` (`reg_n_refs += loop_depth`), so the flag
  computed inside one has three references (0.107) at no instruction cost. The scope has to hold *only the computation of
  the flag*: around the whole `if`/`else` it is a loop whose `else` arm ends in the exit jump, jump.c's "if (foo) bar;
  else break;" rule (`jump.c`, "Look for if (foo) bar; else break;") swaps the arms and moves the then-arm after the
  jump table (3260), and around the flag test plus the stores after it the scheduler freezes the stores in place (the
  loop notes are scheduling barriers).
- **Step 4: the arms' own registers.** The target's arms have the word in `$v1`, the mask in `$a0` for most arms and
  the other way round in the arms with a `D = 1;` store; the shared `var_v1`/`kind_mask` pseudos (22 references,
  global.c) gave every arm the same pair. Written arm by arm the pseudos are block-local and `local-alloc.c` colours
  them per arm, and `jump2` merges the identical tails again, as the target shows. The order inside the arm matters
  for the first scheduler: `{ s32 v = X; v &= M; v |= K; ... X = v; }` loads the word before it builds the mask
  (`lw`, `lui`, `ori`, as the target), `X = (X & M) | K;` as one expression builds the mask first, and the arm's
  other statements (`kind` byte, a counter, `var_s5`) sit where the target has them only between the `|=` and the
  store (mixed). This is observation of one function, not a rule.
- **Step 3: a `volatile` load is never moved into a delay slot** (the branch above it then has a `nop`, and the
  jump-table entries below are one word off: each table row scores 150). Whether the qualifier belonged to the game is
  unknown; the family offers it and the judge decides.
- Steps 1, 2 and 3 are each worse alone (2520/2470, 2465, 2560): the chain takes them together only through the
  combined child (1) or after the step before it (2, 3).

Failed experiments that would otherwise be repeated:

- `do { if (arg15 flag) {..} else {..} } while (0)` (3425), with the arms swapped (3145), around the whole `if` plus
  the next store (2350, registers right, the stores' order wrong), and `while (flag) { ...; goto done; }` (the loop
  notes are deleted: 89's references stay 2).
- `arg15 = 1;` as the carrier of the constant `1` stored later (2770): cse replaces the stores' operands by the
  constant, the `QImode` constant and the parameter are different pseudos, and a third pseudo takes `$s6`.
- The arms moved to the compound form one family of arms at a time scored 2505 (plain arms, mask spelled in one
  expression, registers wrong), 1570 (compound form and the flag scope), 1450 (the flag computed in its own scope),
  1330 (the two arms that call), 1230 (the arm that sets a `var_s5`), 535 (the arm with a counter), 415 (the two arms
  with a global store), 460 and then 0 (second switch's range, then the plain `0x5C` word: the `nop` the `volatile` left
  moved every table entry). Statements of an arm outside the update's block (the counter and the kind byte of the arm
  with a counter) scored 535 where inside it scored 0 with the others: the mixed order is not a free choice.

What is controlled evidence and what is one function:

One function (verified by `compile_and_diff`: 713 instructions byte-identical). The priorities are read from
`passes.py` (`-dl -dg`) and `explain_regs.py`, not instrumented in the compiler. The match relies on one scaffold: the
`do { } while (0)` around the flag's computation (a weight, no instructions) and on `arg15_flag`, a local the game
probably did not have; everything else is plain source. The decomp-repair tests are synthetic reductions of each step.
