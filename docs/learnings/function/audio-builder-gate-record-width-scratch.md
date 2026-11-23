---
title: audio builder gate record width scratch
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

A 616-instruction audio command builder (N64, KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): it takes one record by
pointer, reads its fields as bytes, halfwords, words and a double, builds a six-word run on the stack in four ways and
writes one 0x50-byte command. Saved best 8692 (exact judge, scoring version 2); no existing family moved it (the whole
library's best single child from it was 8332). Not matched: 3985 after the steps below, the rest being the
two-instruction `andi`/`andi` byte form (below) and the registers that follow from it.

Steps, each measured with the exact judge from the previous one (alone / in combination as noted), each now a family:

| step | before -> after | family (decomp-repair 0.5.83x) |
|---|---|---|
| arm scratch bytes folded into the function-wide byte local that takes over, the head's shared scratch given one name per arm (the `$s0` quantity of the head was taken by an arm-local byte; arg/record pointers then moved by one register) | 8676 -> 8456 together (10806 and 10561 apart) | `arm_scratch_merge` |
| m2c's `(flag = a < b, x) \|\| (flag = a < b, y)` gate as `(x \|\| y) && !(a < b)`, and the volatile word re-read in the `if` read from the local that holds it, after the sum that also reads it | 8456 -> 7996 (8246 / 8206 apart) | `comma_gate_plain`, `held_volatile_reread` |
| the first table pointer given its own local (the same local was reassigned at the end: a global pseudo took `$a3`, the count `$v1`) | 7996 -> 7981 | `local_pointer_web_split` (existing family for the shape; not run on this function) |
| the `void *` parameter as a record pointer (two member loads then go above the store of the call's result: sched.c treats a member read as another object than a scalar global) | 7981 -> 7861 | `param_record_view` |
| `if (x == 0) { A } else { B }` as `if (x != 0) { B } else { A }` (the target's `beql` fills the delay slot from the arm that is second in the C) | 7861 -> 5645 | `branch_orientation` (existing: its fourth child of the 7861 source is this one; from the saved best it scored worse than the base) |
| `*((s32 *) p) = (s16) v` as the `sh` the target has; `v = (s16) ((s32) f); *((s16 *) p) = (s16) ((s32) f);` as `v = (s32) f; *((s16 *) p) = v;` | 5645 -> 5245 -> 5035 | `narrow_store_width`, `narrow_roundtrip_hold` |
| seven adjacent independent statements (stores to the stack run, a halfword store) moved down one line | 5035 -> 3985 | `adjacent_array_store_order`, `adjacent_cast_store_swap` (existing: from the 5035 source their children score 4665 and 4785 alone; the hill climb that reached 3985 did seven moves one at a time) |

Hypotheses read off the compiler's behaviour, not confirmed in its source: the head's arm-local byte is a block-local
quantity that local-alloc puts in `$s0` before global.c allocates (`cc1 -dl` shows 1-byte block-local pseudos there);
a typed member read is not ordered after a store to a scalar global by sched.c's dependence test.

Not solved: the target writes each byte value as two instructions, `andi $v1, $v0, 0x3f` then `andi $s4, $v1, 0xff`, which
no spelling tried here keeps (`(x & 0x3F) & 0xFF`, `(u8) (x & 0x3F)`, a `u8`/`u32`/`s32` local, `register`, an inline
helper returning `u8`, a bit-field read, `% 64`, a shift pair): combine folds the `and` into the zero extension each time. In a
microtest the pair survives when the `and` result has a second use, and no second use shows in the target, so what the
original wrote that keeps it is open. Each of its eight sites also costs the registers after it.

What is controlled evidence and what is one function: every step's before/after is one function, measured with the
exact judge; the library's tests use synthetic C. The families are not known to help other functions yet.
