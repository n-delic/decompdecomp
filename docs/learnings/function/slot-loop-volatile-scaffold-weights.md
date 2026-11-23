---
title: a slot-reset loop saved with volatile and inline scaffolding, and two loop weights
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like and the exact compiler and flags: a 184-instruction N64 save-slot reset function (KMC GCC
2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`): a prologue that clears a few globals and starts a command, then a four-slot
`for (;;)` that zeroes four per-slot tables, tests three fields of the slot's record and dispatches a switch on a
verify call. Saved best 455 (judge 2.0 scoring; the recorded historical 325 was an older scale), 14 differing rows,
and no repair family fired on it: the saved best had `extern volatile s16 table[4]` (a volatile table the loop stored
to), `inline static int inline_fn(int a) { return -a; }` called as `inline_fn(1)` for three `-1`s, `&flags[i]` pointers,
`i * 4` recomputed in three lookups, `if (0) { label: ... }` and a `do { } while (0)` fence.

The source change, and the measured result before and after (scores through the app's judge, 455 -> 0). The steps, in
the order they were found, each with its score alone and combined:
1. `inline_fn(1)` written `-1` (the stores' `-1` then loads in a register of its own, `addiu $v0,-1` among the prologue
   insns beside the `$s0` that holds `state`): 455 -> 345 on the stores only, same score for every call.
2. `volatile` dropped from the table (the store becomes `lui $at; addu $at,$at,$v0; sh %lo()($at)`): 455 -> 540 alone,
   a worse score because the lbu chain then hoists above the table stores; right only with the rest.
3. The slot's pointers written as integer sums, `(s16 *)(i * 2 + (s32) flags)`: the scaled index first in the add
   (`addu $a2,$v0,$s3`; the pointer-first `&flags[i]` always gave `addu $a2,$s3,$v0`): 70 -> 60.
4. `s32 j = i * 4;` right after the pointer locals, the lookups reading `j`: puts the `sll` among the first insns, 530 -> 430
   with 1-3 (all four together 455 -> 430, the first child a greedy chain can take).
5. The compound `if` that holds the lookups wrapped once in `do { } while (0)`: 430 -> 30 (offset refs 9 -> 11 against the
   slot's 9; the offset takes `$a0` and the slot `$a1`, as in the target).
6. One table store wrapped twice: 30 -> 0 (the shared `i * 2` temporary gets `$v0` and the pointer beside it `$v1`).
The match relies on scaffolding: two `do { } while (0)` weights (steps 5 and 6) and the `if (0) { label: }` the saved
best already had; polishing removes them.

Why (read in global.c, local-alloc.c and sched.c; hypotheses read from the `-dl -dg` table, not confirmed by anything the
compiler says): a loop note makes the first insn after it a full barrier for the first scheduler (sched.c,
`sched_analyze_insn`), so the `sll` is kept where it was emitted and `j` is what moves it; global.c allocates by
`floor_log2(refs) * refs / live`, and `j` (live 16) needed 11 weighted references against the slot's 8-9 (live 13) to
come first; local-alloc gave the shared index the lower register once its stores weighed more than the pointer's.

Failed experiments that would otherwise be repeated: `j` right after the slot's load (250, the `sll` ahead of the `sll`
for `i * 2`); the `j` weights only on its own definition (the slot rises with it, the statement names both); the
exhaustive 0-2 wrapper search over the pointer and store statements with no `if` wrapper (best 40); the natural
`gSaveSlotFlags[i]` spelling instead of the hoisted pointers (1873, the loaded table is not hoisted); `phantom_wide_local`
reaches 0 from the 30 as well (a `long long` block) and is not a faithful program: never kept.

What is controlled evidence and what is one function: one function. `prove_chain.py --families all` from the saved best
reproduces it with the library's new families (decomp-repair PR 1283): pure_inline_literal 455 -> 345, byte_index_local
-> 320, statement_scope_weight -> 285, slot_loop_scaffold (the four steps as one child) -> 30, statement_scope_weight
-> 0. Library: `pure_inline_literal`, `volatile_array_plain`, `pointer_index_int_form`, `byte_index_local`,
`slot_loop_scaffold`, `statement_scope_weight` (all declare REPAIR).
