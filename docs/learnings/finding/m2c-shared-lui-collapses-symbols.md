---
title: m2c names two globals reached through one lui by the first one; SDK boot routine chain
kind: finding
compiler: gcc-2.7.2-kmc
evidence: verified
date: 2026-11-27
---

A 138-instruction N64 SDK boot routine (libultra initialisation), `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`,
whose function profile was a different compiler and whose saved best stood at 8959 after 32 attempts.

m2c (MIPS) reads `lui $a2, %hi(D_A)` followed by `%lo(D_B)($a2)` as another access of `D_A`: the draft stored
`D_80000000` four times (and read `D_80100000 * 2` for two different globals). Writing `%lo(D_B + 4)` or `D_A + 4`
collapses the same way. A `lui $at, %hi(D_B)` of its own before the access gives the right draft
(`m2c_shared_hi.split`). Controlled on a synthetic assembly: two stores and two loads.

The chain from a fresh draft with that fix, each step a decomp-repair family, judged by the app's judge:
`symbol_block_copy` (the four exception-vector copies as struct assignments) 10385 -> 3870, `mmio_address` (the three
AI register stores `volatile`) 2645, `u64_pair_libcall` (the clock rescale as `clock * 3 / 4ULL`),
`target_call_arity` (a stray third argument), `join_tail_arm_inline` (the video clock stored in each arm; it declined
on the volatile writes until this fix) and two `branch_orientation` swaps: 0. From the saved best the exact C is the
SDK's own shape (struct assignments, `while` loops on the SI reads, an `if`/`else if`/`else` storing in each arm).

Observed: `prove_chain` takes `mmio_address` before `join_tail_arm_inline`, which is why the arm inline has to work
with the volatile writes present. A `*(unsigned long long *)&global` cast spelling of the rescale scored worse than
retyping the declaration. One function; the draft collapse is general.
