---
title: loop.c hoists a float constant out of a loop; one shared named local keeps it where it is used
kind: finding
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: controlled
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

A ~1000-instruction collision sweep (nested object-list loops with six inner triangle loops, each calling a helper
and then testing `out >= 0.0f && out < best`, and a seventh compare after the loops), KMC GCC 2.7.2
`-O2 -G0 -mips3 -mgp32 -mfp32`. The target loads the zero with `mtc1 $zero,$f0` right before each compare, in every
loop. Every candidate instead kept the zero in a callee-saved FP register (`$f22`/`$f24`, `mtc1 $zero,$fN` before the
loops), which costs one more saved FP register, an 8-byte larger frame and shifts every later FP register choice.
Earlier saved bests worked around it with `(x + 1) >= (0.0f + 1)`, which only moves the problem to a hoisted 1.0f.

The rule, confirmed in the compiler source (the `gcc-2.7.2-kmc-source` tree, loop.c; a community reconstruction, not
KMC's own sources, but the decisions below are in code that is unchanged in 2.7.2):

- Expansion of `x >= 0.0f` forces the zero into a register: `(set (reg) (const_double 0))` with a REG_EQUAL note,
  placed just before the compare.
- `scan_loop` makes it a movable when the pseudo qualifies. Three cases decide it (the `else if (! ((! maybe_never &&
  ! loop_reg_used_before_p (...)) || (! REG_USERVAR_P (...) && ! REG_LOOP_TEST_P (...)) || reg_in_basic_block_p
  (...)))` test): the set is before any label or jump of the loop and the reg is not used earlier; or the pseudo is a
  compiler temporary (not a named local); or `reg_in_basic_block_p`: `regno_first_uid` is this insn and
  `regno_last_uid` is in the same basic block. A temporary is therefore always movable. The pseudo of a named C local
  is movable only when its set and every use are in one block (a `maybe_never` set, one after any jump or label of
  the loop, never passes the first case).
- `move_movables` moves it when `already_moved[regno] || threshold * savings * lifetime >= insn_count`, where
  `threshold = (loop_has_call ? 1 : 2) * (1 + n_non_fixed_regs)` (61 here, with a call in the loop), `savings` is the
  number of sets, `lifetime` the span in luids of first to last reference and `insn_count` the loop's real insns when
  the pass runs. `threshold -= 3` after each movable moved in that loop pass; `-dL` prints "moved" for loops of up to
  61 real insns (fewer after another move) and "not desirable" for larger ones.
- `combine_movables` groups identical constants of sibling loops (each pseudo set once in the loop, not global): the
  group head gets the summed savings and lifetimes, so four zeros in a 395-insn middle loop pass (61 * 4 * 4 >= 395)
  even when every inner loop was padded past the threshold.
- Once an inner loop moves the constant, its pseudo spans the whole nest (`lifetime` = a few hundred), so every
  enclosing loop moves it too (`halved since already moved` only doubles `insn_count`).
- `find_and_verify_loops`/`mark_loop_jump` mark a loop invalid (nothing is moved or strength-reduced) when a jump from
  outside it enters its body, or a setjmp or a label address sits in it; loop notes stay, so loop-depth weights
  remain. (A `goto` into a loop did keep `mtc1 $zero,$f0` in the loop in a probe; a faithful program rarely has one.)

The source change, and the measured result before and after (scores, the judge used):

One `f32` local declared at the function top (no initialiser) and assigned in place at every compare:
`out >= (zero = 0.0f)` (or `zero = 0.0f, out >= zero`). Its pseudo has references in many blocks, so loop.c never
moves it, at any loop level; cse still sees the constant at the compare, and the final code loads the zero where it is
compared (`mtc1 $zero,$f0`) with nothing left of the local. On the collision sweep from its saved best with plain
`>= 0.0f` at the seven compares: 8632 -> 7018 with nothing else changed, and the prologue's saved FP registers, the
frame size (0x1e0) and `mtc1 $a3,$f22` became the target's. An initialised declaration (`f32 zero = 0.0f;` at the top)
works too but cse then forwards that constant into unrelated literal stores (7743).
Library: decomprepair `float_constant_loop_pin` (0.5.624), wired into the drafts and the Deep Permuter's local lane.

The same function then needed the earlier frame recipe; each step measured with the judge:

- Aggregates are 8-aligned; spill slots sit at 8k+4 offsets (0x12c, 0x134, ...), allocated in the order of the spilled
  pseudos (a parameter first: copying `arg0` into a local made the copy take the late slot, removing the copy and a
  `volatile` local moved all slots to the target's; 7018 -> 6434).
- An address-taken scalar is placed late, while an array or struct sits at its declaration: the scalars the target has
  in the middle of the locals are `f32 x[2]`.
- Locals the target overlays (a rotation matrix, a 3-short angle array and a vector at +0x10 and +0x18) are one
  union; the frame gap before the next local was a `char pad[0x30]` member (6434 -> 6118 with every local offset
  equal to the target's).

Failed experiments that would otherwise be repeated:

- `-fno-strength-reduce`, `-fno-expensive-optimizations`, `-fno-cse-follow-jumps` and other flags change nothing about
  the hoist (score or `mtc1` position).
- Float compare spelling (`0.0f <= x`, `!(x < 0.0f)`), copies and casts in the loop body, a user local declared inside
  the loop body (`f32 z = 0.0f;` is set and used in one block, so it is movable), `while`/`do`/`continue` loop forms,
  a switch (its table label is outside the loops), a do { } while (0) around goto loops (the wrapper loop is scanned
  and hoists): all still hoist.
- Padding the inner loops past 61 real insns stops only the inner move; the middle loops move the group (and an outer
  `goto` loop stops the hoist but loses the loop-depth weights, 8673 -> 16883).
- Returning the object-skip flag from an `inline` helper (to get the target's `$a0` temp copied into `$s2`) compiled
  `return type != 0x3F` as `xori`/`sltu` and scored worse (6118 -> 7133); constant 1 held in a flag local is folded by
  cse.

What is controlled evidence and what is one function:

The loop.c reading (the three movable cases, `threshold`, `combine_movables`, `mark_loop_jump`) is read in the 2.7.2
source and each decision was reproduced with small programs under the pinned compiler (`-dL`: moved / not desirable /
ignored due to multiple entry points; `mtc1 $0,$f22` hoisted versus `mtc1 $0,$f0` kept). The 8632 -> 7018 measurement
is one function. The claim that the target's source had such a local is not established: the shape is a faithful way
to reach the target's code, not a recovered source.
