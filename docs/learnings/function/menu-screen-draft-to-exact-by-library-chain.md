---
title: A menu screen from its m2c draft to exact through library families alone
kind: function
compiler: SN64 GCC 2.8.1 -O2 -mips3 -mfp32
evidence: verified
date: 2026-11-28
---

A 461-instruction N64 menu screen (pause and confirmation menus drawn with a string routine and a few display-list
commands). Saved best: the m2c draft, 17599 after many attempts. Project's per-function profile `-G0 -mips3 -O2 -mgas
-meb -mcpu=VR4300 -mhard-float -mfp32`, unchanged. The exact judge decided every score.

## What matched it

A typed rewrite from the assembly scored 680 on the first compile; the one edit left was `selected = 0;` above the
guard that holds `if (i == chosen) selected = 1;` (inside the guard GCC folds it to `xor; sltiu`), as the draft had it.

Then, from the draft again, the same result by small edits and by decomp-repair calls (PR RetroReversing/decomp-repair
2312). The calls, in the order applied: `goto_paths_written_out`, `float_register_variable_argument`,
`global_command_run`, `stack_extent_repairs`, `stack_object_size_repairs`, `stack_value_run_array`,
`pointer_scale_repairs`, `symbol_stack_block_copy`, `asm_table_index_read`, `target_call_arity` (twice),
`cursor_deref_index`, `integer_cursor_for_loop` (twice), `float_literal_global`, `tail_call_arm_repairs`,
`loop_counter_unify`, `strided_element_record`, `pointer_local_merge_repairs`, `address_indexed_global`: end state 0.

Leave-one-out on the hand edits of the draft (all others applied, 6395): without the float retype 11035, the stack byte
pair 9551, packet commands 8330, the aggregate copies 8050, the byte table 7555.

## Rules

- m2c prints the cursor's *entry* value by the global's name after it has stored the advanced cursor, so a draft's
  batched display-list run is wrong C as written (one word lands a command too far).
- A packet through `(*(Cmd **) &cursor)++` keeps the old value as a register copy; `p = cursor; cursor = p + 8` does not.
- A float passed after integer arguments shows as `lwc1` then `mfc1 $aN`: the variable, its pool word and the parameter
  are all `f32`.
- Three byte loads and three byte stores off one base is a 3-byte struct or array initialiser; copied from a word-typed
  label it loads a halfword instead.

## Not established

The chain was applied in a chosen order by hand. No greedy chain or Deep Permuter run was made, so whether a search
finds that order is not shown. `integer_cursor_for_loop` repeats a stride-one cursor's start without its stability
check: on this draft that would reread a global table slot on every pass, so `cursor_deref_index` was applied first.
