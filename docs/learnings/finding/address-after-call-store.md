---
title: a store at a global table's base plus a scaled index wants the call first, then the base in a pointer of its own, then the sum
kind: finding
compiler: SN64 GCC 2.7.2 (sn-n64-281), -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-24
---

What the function looked like: a 20-instruction N64 function that stores a call's result into the first word of a
16-byte slot of a global table, a parameter into its last word and returns 1. m2c writes the address before the call
(`slot = (index * 0x10) + &TABLE; M2C_FIELD(slot, s32 *, 0) = helper(arg);`, 925) and the search left it as one
expression with the address assigned inside the left side (`M2C_FIELD((slot = (index * 0x10) + TABLE), s32 *, 0) =
helper(arg);`, 60, usually inside `{ { } }` fences). At 60 the compiler emits the shift (`sll`) before the table's
`lui`/`addiu`; the target has the table's two instructions first and the shift after them, both after the call.

The source change, and the measured result before and after (scores, the project judge): the developer's C is three
statements where the saved best has one,

    value = helper(arg);   base = TABLE;   slot = base + index * 0x10;   M2C_FIELD(slot, s32 *, 0) = value;

60 -> 0. Controls from the same start: the call hoisted alone 60; every spelling that keeps the table in the same
expression as the shift 60; `symbol_address_local`'s pointer (set ahead of the whole statement, so ahead of the call) 656;
the base and the sum computed before the call 925 (`arg0` spilled differently); the base held across the call
(`base = TABLE; value = helper(arg); slot = base + ...`) 656.
Library: decomp-repair `address_after_call` (v0.5.1012), children `address_after_call` (the matching order) and
`address_base_before_call`. `prove_chain --families all`: from the scaffolded saved best 60 -> 0 in one step; from the
chain seed 60 -> 0; from the m2c draft 925 -> 150 (`address_after_call`) -> 0 (`integer_pointer_sum`, which retypes
the draft's `M2C_UNK` scalar so the sum counts bytes).
Why the base's own statement matters (the order the scheduler first sees the `lui`/`addiu` and the `sll`) is a
hypothesis the measurement agrees with; the judge decided.

Failed experiments that would otherwise be repeated: moving the call before the address computation alone; the table's
address and the sum before the call; `&TABLE[index * 0x10]` before the call; the table in a pointer ahead of the statement.

What is controlled evidence and what is one function: one function, verified by the project judge (0), with the
single-edit controls above. False-positive load of the family: it fired on one of 2,206 functions of a project (4,620
saved sources and drafts, nothing judged), the draft of the function it came from, and on no matched or saved best, so
it is cheap to keep in the draft path. Not covered: a table reached through a pointer global, an index that is a
global, a store that is the body of an unbraced `if`, and a call whose arguments read the pointer.
