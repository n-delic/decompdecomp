# Finishing a near-match

A near-match is a function whose best score is small (about 50 or less) or whose remaining rows are only `r`
(registers) and `o` (order). Helpers have spent hundreds of attempts on such functions, each trying one spelling,
seeing no change and handing over the same dead ends. The ones that matched were finished differently:

## Before searching: clear the ground (five minutes)

1. **Remove inherited workarounds first.** Earlier agents' `volatile` locals and reads, frame-padding arrays,
   scratch unions sized to the frame and phantom locals were often added to buy a few points; they also block the
   repair families (several decline on any `volatile`) and the real fix. Take them out, compile, and start from
   whichever of the two is lower.
2. **Check the function's own compiler profile**, not only the project's (the brief's "compiled with" / "compiler
   ID" lines). Every access as `lui $at; addu $at,$at,$sN` with stepped saved registers means
   `-fno-strength-reduce`; a function wrongly set to it can never reach strength-reduced counter multiples. An
   `sra` after a byte or halfword load means IDO, not GCC. `compiler_experiments` tests a profile without applying it.
3. **Look for a matched twin:** `find_similar(function)` lists matched look-alikes with their saved C. Adapt their
   spelling (loop form, temporaries, casts) before writing your own.
4. **Search the evidence by symptom:** `search_learnings("register swap")` (or "store too early", "frame size",
   "hoisted constant") returns the recorded fixes and the repair families that make them; `prove_chain` runs those
   families greedily on your revision.
5. **Expect a combination.** A late match is usually two or three edits that are each neutral or worse alone. Keep
   them, then send them together (`compile_variants(combine=true)`, `score_permutations`).
6. **Faithful programs only.** No stores to wrong addresses, dead blocks or unused `long long` locals to move a
   score; when a match still relies on scaffolding (a dead local for a frame slot), say so in the notes.
7. **Read the tables before you search.** For register rows read the allocation table, for order rows the
   scheduler's order (`compiler_passes`); a grid without a reason burns compiles.

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
3. Read that pass's source to learn which input it keys on. `compiler_passes` ends with where the compiler's source
   is (`toolchains.source_for(compiler)`: the toolchains folder's `<id>-source`, downloaded with the compiler): the
   passes are `jump.c`, `cse.c`, `loop.c`, `combine.c`, `local-alloc.c`/`global.c`, `sched.c`, `reorg.c` (in `gcc/`
   from EGCS on). It is the nearest public release or a community reconstruction, as its line says; vendor builds
   (KMC, PSY-Q, SN, Sony's EE and PSP) may differ. Proprietary compilers (IDO, Metrowerks, SN's own, Visual C++) have
   none.

## Register-only rows: read the priorities

When only saved registers are exchanged (`$s1`/`$s2`, everything else right), don't guess: `compiler_passes`
returns the allocation-priority table (uses, live length, priority, register). GCC 2.x hands out saved registers in
order of `floor_log2(uses) * uses / live_length`, so the fix is one use more or less, or a shorter live range, for
the value that must rank higher: index a loop by its counter instead of a separate offset, guard a loop on the
counter (`if (i < n)`) rather than on the bound, give a reused name its own temporary.

<!-- only: mips -->
## GCC 2.x rules that decide the last rows

Each rule names the pass that applies it and its evidence level (verified: matched with it in the app's judge;
controlled: measured on a reduced case; observation: one function, mechanism read in the source; hypothesis: seen,
not confirmed). Measured on KMC GCC 2.7.2 `-O2 -G0 -mips3`; the passes are the same in other GCC 2.x builds.
`compiler_source(compiler, "<function>")` prints the pass function named here.

### Register-only rows

- **Saved registers go by priority** (`local-alloc.c`/`global.c`): `floor_log2(refs) * refs / live_length`, refs
  weighted by loop depth (`flow.c`). Fix the value that must rank higher: one use more or less, a shorter range.
  *verified, several functions.*
- **`do { } while (0)` adds loop-depth weight** to every reference inside it at no instruction cost: wrapping a
  value's set or use raises it, removing a wrapper (even one a `goto` crosses) lowers it. *verified (a scan loop,
  20 -> 0 by removing one wrapper); the flow.c reading is a hypothesis.*
- **Equal priorities break by birth order** (`local-alloc.c` `qty_compare`): the quantity born first wins, so moving
  a set earlier or later swaps two equal locals. *observation (a ray test, 70 -> 0).*
- **Argument-register copy preferences** (`global.c` `find_reg`, `set_preference`): a value copied plainly into
  `$aN`/`$v0` is given that register over a lower free one; a copy through a `subreg` or an extension (a `u8`
  result) is only a plain preference and loses to the lowest free register. Change the copy's width to move it.
  *observation.*
- **`REG_EQUIV`** (`local-alloc.c`/`reload1.c`): a local set once to a constant or a fixed address gets a REG_EQUIV
  note and is rematerialised at each use instead of taking a register; a second set removes the note. *controlled.*

### Order rows (a store or load in the wrong place)

- **sched1 boosts a single-set pseudo** (`sched.c` `birthing_insn_p` in `adjust_priority`): an insn that sets a pseudo
  with one set, live afterwards, jumps to the launch priority and lands right before its use. Give the value a second
  set (one temporary assigned in both arms) to drop the boost. Statement order alone does not move it. *observation
  (60 -> 0).*
- **Struct member versus raw cast** (`alias.c` `true_dependence`): `*(s32 *)((char *)p + 0x74C)` may alias any scalar
  global, so it waits behind an earlier global store; `((struct { char pad[0x74C]; s32 f; } *)p)->f` is MEM_IN_STRUCT
  and cannot conflict with a fixed-address scalar, so it moves above it. *verified (60 -> 0).*
- **A loop note is a scheduling fence** (`sched.c` `sched_analyze_insn`): the first insn after a `do { } while (0)`
  depends on everything before it. End or start a one-shot scope between two loads to keep a store between them.
  *verified (70 -> 0).*
- **Hoisted loop constants** (`loop.c` `move_movables`): a set is moved out of a loop when
  `threshold * savings * lifetime >= insn_count`, `threshold = (call in loop ? 1 : 2) * (1 + free regs)`, minus 3
  for each one already moved; `combine_movables` merges equal constants into one register. A compiler temporary is
  always movable; a named local only when its set and uses share a block. Name the constant to keep it in place,
  write a literal to let it float. *controlled.*

### Frame-size rows (`addiu $sp` and every `sp` offset differ)

- **Each spilled pseudo takes an 8-byte slot** at the end of the locals, in pseudo-number (declaration) order.
- **A dead pseudo can still take a slot**: a local cse folds away (`zero = 0; if (n > zero)`) keeps one from its stale
  reference count, emitting no code. It is scaffolding: report it. *observation (30 -> 0).*
- Fix the frame before anything else: every other row shifts with it. Trim m2c's frame unions to the target's size.
<!-- end -->

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
