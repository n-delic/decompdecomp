# Finishing a near-match

A near-match is a function whose best score is small (about 50 or less) or whose remaining rows are only `r`
(registers) and `o` (order). Helpers have spent hundreds of attempts on such functions, each trying one spelling,
seeing no change and handing over the same dead ends. The ones that matched were finished differently:

## Keep a numbered edit list, and combine

- Start from the best saved attempt and change it by small, recorded steps. After each compile, `add_note` one line:
  `#3 sink v=K to the join: 14 -> 14, new diff (r rows moved to s1/s2)`.
- **Several edits that are each no better alone are the normal shape of a late match.** A compile that keeps the
  score but changes the diff counts as progress (the move-on count resets). Collect those edits and send them
  together: `compile_variants(..., combine=true)` with 2–5 patches tries the combination for you.
- Never repeat an edit the brief's "already tried" list or a handoff says was judged at this best.

## Trace the compiler instead of guessing

Three or four structurally different sources landing on the same score means a compiler pass decides it, not your
spelling. Stop writing variants:

1. `compiler_passes(function, revision, passes=[...])` (GCC-family compilers) dumps the compiler's own stages for
   your candidate: after expansion, jump optimisation, cse, loop, register allocation, scheduling, delay slots.
   Compare a variant that moved the difference with your best: the first stage where they differ is the pass that
   decides it, and that pass's input (a use count, a fall-through, a constant in a register) is the C to change.
2. Read which stage the target's instructions come from: a `li; j` pair in a fall-through is jump-optimiser output,
   an extra move is register allocation, a nop or a moved instruction in a delay slot is delay-slot filling.

## Register-only rows: read the priorities

When only saved registers are exchanged (`$s1`/`$s2`, everything else right), don't guess: `compiler_passes`
returns the allocation-priority table (uses, live length, priority, register). GCC 2.x hands out saved registers in
order of `floor_log2(uses) * uses / live_length`, so the fix is one use more or less, or a shorter live range, for
the value that must rank higher: index a loop by its counter instead of a separate offset, guard a loop on the
counter (`if (i < n)`) rather than on the bound, give a reused name its own temporary.

## Branch polarity from delay slots

Which arm of an `if` is out of line (holds the `goto`, comes after the function's main path) shows in the target's
branches and their delay slots: an instruction from one arm sitting in a branch's delay slot belongs to the arm the
branch falls into. Write the condition so that arm is the one the compiler keeps in line; an assignment before the
`if` that the target does in the join belongs at the join (written as its literal), not before it.

## Shapes that have worked

- Recompute, don't cache: if the target recomputes `n & 1` at the use, a `temp = n & 1` local is wrong.
- Statement order is worth a small grid (3–4 statements, a few dozen orders: `score_permutations`); declaration
  order, `register` and integer-width hints usually give identical code: test one, don't grid them.
- A copy in the wrong register beside a `goto` join: put `v = s;` at the end of each arm, not before the guard.
- A commutative operation whose constant must come first: hide the constant from cse with `(x & mask) | mask`.
- A `do { } while (0)` is a scheduling boundary for GCC's second scheduler; empty `while (0)` and `goto` vanish
  before scheduling and do nothing.
- Base-relative stores stay relative only when the symbol's first address use is wrapped in `do { ... } while (0)`.

`try_local_repairs` runs the repair families that encode these (`join_initializer_sink`, `branch_polarity`,
`join_copy_sink`, `shared_result_copy`, `mask_operand_order`, `scheduling_scope`, `branch_slot_scope`, `chain_load_scope`, `halfword_countdown_shapes`, `retry_goto_scope`, `guarded_offset_index`,
`compound_global_update_repairs`, `operand_order_pass`): run it on your best revision before hand-writing them.
