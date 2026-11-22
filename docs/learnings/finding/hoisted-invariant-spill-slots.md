---
title: hoisted invariant spill slots
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

A ~110-instruction N64 function with a loop over two large stack structs, calling out four times per iteration, under
gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`. The target stored `n - 1` and `flags & 1` to two stack words before the loop
and reloaded each with `lw $v1` inside it (`addiu $v1,$s2,-1; sw $v1,0xc74($sp)`), while `flags & 2` and `flags & 4`
sat in `$fp` and `$s7`. m2c had written the two words as fields of a frame union (`view_spC74`, `view_spC7C`), which
gives `$v0` and the wrong frame; the saved best scored 379.

The source change, and the measured result before and after (scores, the judge used):

The two stack words are not variables: they are loop-invariant pseudos that loop.c hoisted (`n - 1`, `flags & 1`) and
global.c then lost the race for the nine saved registers (lowest priority, fewest references), so reload spills them
and uses `$v1` as its spill register. Write the expressions inline in the loop (`var_s1 == temp_s2 - 1`, `arg1 & 1`)
or as plain locals assigned before it, shrink the frame union by the two words (3168 bytes), and the code is the target's
(379 -> 30, standalone judge). Source rules found on the way (each measured):

- Each spilled pseudo takes an 8-byte slot (`assign_stack_local` with align -1 rounds the size to BIGGEST_ALIGNMENT) at
  the end of the locals, in ascending pseudo number order. A union sized to the frame (3192) therefore double counts them.
- A *dead* spilled pseudo also takes a slot: a local assigned a constant before its use (`zero = 0; if (n > zero)`)
  that cse folds away keeps a slot (stale `reg_n_refs`), though it emits no code. The target's third slot (frame 8
  bytes larger, no access) was this. Slot order follows pseudo numbers, so the dead local has to be declared after the
  locals whose slots must come first (`lim`, `flag`, then `zero`); declared inside the loop body it gave no slot. This is
  scaffolding and is reported as such; 30 -> 0.
- With the invariant as a plain local the `&&` of two simple compares became branch-free (`sltiu; xor; and`); nested
  `if`s give the target's two branches.
- An explicit `p = base; ... p += 4` pointer made `move $s3,$s4` come before the hoisted ops; indexing
  `((f32 *)base)[i + 3]` lets loop.c's giv initialisation come after the movables, as in the target.

Failed experiments that would otherwise be repeated:

Keeping the words as union fields and nudging registers (505, `$v0` always); `do { } while (0)` wrappers around the
hoist (they raise the invariant's weight so it takes `$s5` and the arguments move); `char pad[8]` first (shifts every
buffer); about 4000 type/`register`/expression mutations, none of which added a slot.

What is controlled evidence and what is one function:

One function. The slot-size and slot-order rules are read from reload1.c/function.c and confirmed by the cc1 `.frame
vars=` figure on the variants; the dead-local slot is one measurement (it appeared with a function-level declared
constant local, not with one declared in the loop). Hypothesis: stale reference counts, not confirmed in the source.
