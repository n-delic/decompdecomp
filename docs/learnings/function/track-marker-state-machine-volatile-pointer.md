---
title: track marker state machine volatile pointer
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

A 349-instruction N64 menu/transition state machine (a `switch` of about twenty cases with m2c `goto block_N` joins
and a global pointer `D_80109688` declared `extern void *volatile`), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`,
saved best 555, matched (0, confirmed by `compile_and_diff`).

The edits from the saved best, each scored by the app's judge-worker (`fast_judge`) on the previous edit's result:

1. the two arms that set a float constant for the next state, in the target's code order (the case labels
   swapped): 555 -> 535. Already offered by `measured_case_float_sign`.
2. the arm that reads the pointer a second time directly (`temp = ((T *) (G))->f;`, a volatile load) and stores
   through the first pointer local: read it into the local (`marker = G; temp = marker->f;`) so the store goes through the
   pointer the compare read: 535 -> 320 (`volatile_pointer_reread`, the assign form). The first read stays a
   non-volatile read through a cast of `&G`, as before.
3. the float temp that two arms of the switch share given a name per arm (`temp_fv1_b`): 320 -> 300. One pseudo for both
   arms outranks the other arm's compare operand, so the `c.lt.s` operands were swapped and the constant took the
   lower float register (`case_temporary_nested_split`; `case_temporary_split` declines: labels in the switch, the assignment
   nested in a block).
4. the condition read of the other arm folded into its pointer local (`(temp = marker->f) <= 0.0f` instead of a second volatile
   read of `G` right after `marker = G;`): 300 -> 60 (`volatile_pointer_reread`, the fold form; the two sites together are
   one child, 535 -> 80).
5. two arms end `((T *) G)->f = 0.0f; goto block_63;`. Cross-jumping merged them unless one arm kept a `do { call(); } while (0);`
   fence, which also put the `li $a0` in the call's delay slot where the target has a `nop`. The zero stored through an integer
   view (`*(s32 *)&((T *) G)->f = 0;`, the same `sw $zero`, a different RTL mode) keeps the tails apart, and the fence goes: 60 -> 0
   (the fence removed alone: 650; the integer view alone: 60) (`identical_tail_store_mode`, one child).

Failed: wrapping other statements in fences, `var_a0 = 0x12` after the store (the `li` still precedes the volatile load: its
dependence on the loop note), a plain `f = 0.0f` store without the fence (cross-jumping merged the arms: 650).

Controlled evidence: each family's test starts from a reduced copy of the saved best's shape. The sequence 555 -> 0 is one
function; `prove_chain.py` from the saved best reaches 0 with the new families.

Scaffolding the match relies on: the `__repair_record_D_80109688` view struct with two fields, the `TRACK_MARKER` cast macro that
reads the volatile pointer non-volatile, the `register u8 var_v0` and `goto block_N` joins, and the integer-view zero store.
