---
title: two child spawn staged index
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 77-instruction N64 function that sets up a parent entity and spawns two linked children. In each child block the
target stores `child->owner`, then reads the parent's halfword index at once (`lhu $v1`), stores three constants, and
only then adds the offset and stores it (`addiu; sh; andi; sw`). The saved best (m2c, with scaffolding) judged 1285
under this profile and 1525 under the function's earlier sn-n64-281 profile.

The source change, and the measured result before and after (scores, the judge used):

Typed plain C (one struct, `child` reused for both allocations, `first = child` kept for the second block's back link,
`lookupActorIndex(self)`) judged 1010 (app's judge-worker, this profile). With `child->index = self->index + 2;`
written as `u16 idx; ... idx = self->index + 2;` right after the first store of the block, declared in each of the two
blocks, it judged 0. `staged_store_rise` (decomp-repair) emits that as its third child from the 1010 source. The app's
compiler trial under this profile was exact and was applied to this function only.

Failed experiments that would otherwise be repeated:

- One function-level `u16 idx` for both blocks: 40 (the sum lands in `$v0`, the load in `$v1`); `local_web_split` fixes it.
- `idx = self->index; idx += 2;`: 555. `idx = self->index;` then `child->index = idx + 2;`: 165.
- Under sn-n64-281 -mfp64 the 1010 source scored 1395, and the matching source is not exact there.
- Greedy chains of `temporary_hoist`, `temporary_hoist_block`, `store_value_lifetime`, `statement_hoist_over_call`,
  `local_split_new` and `independent_statement_swap` from the 1010 source stopped at 800 (80 judged).

What is controlled evidence and what is one function:

The before/after scores are controlled on one function. That GCC's scheduler leaves the read beside the first store
because the source reads it there is an observation, not confirmed in the compiler's source.
