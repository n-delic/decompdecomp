---
title: object spawner arm and join rewrites
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 795-instruction N64 object spawner (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, an `s16` id and three pointer parameters,
two borders of `if`/`else` arms that build five text records and four marker objects, a switch of five game modes, a sparse
two-case difficulty switch). The saved best was 3693 (m2c's draft plus an earlier agent's scaffolding: `volatile` padding,
one-member `Field_*` wrappers, `if (1) { do { } while (0) }`, a goto to `rejected:`); `prove_chain` from it and from the
scaffold-stripped form found nothing.

The source change, and the measured result before and after (scores, the isolated judge, confirmed by `compile_and_diff`):

A clean rewrite from the target's assembly, then one edit at a time (score after the edit, same judge):

| step | edit | score |
|---|---|---|
| 1 | the two templates are one array (`D_800C7D54[0]`, `[1]`), so the second address is `addiu -0x20` off the first as cse derives it | 6295 -> 5055 |
| 2 | the three constants of each arm set at the arm's head, before its calls (live across them, they take `$s1 $s2 $s5`; the 8-byte frame hole follows) | 5195 -> 4665 |
| 3 | one byte-table read of two symbols in typed rows and the other in bytes (the spill slots of a stale address sum) | frame 0x50 -> 0x48 |
| 4 | `s16` index/pointers written as `(s16) arg0` casts of an `s32` parameter (later reverted to `s16`, see step 12) | 4505 -> 4325 |
| 5 | `y0 + 1` named once (`yy`) | 4325 -> 4030 |
| 6 | the marker results stored through the record field (no `m` local for two of them) | 4030 -> 3660 |
| 7 | a second pointer lookup (`tbl2`) for the second use of the table pointer | 3545 -> 2435 |
| 8 | one value name split (`cv` for the 0x30F word) | 1725 -> 1665 |
| 9 | the call after the `if`/`else` written in each arm (the two `jal`s cross-jump) | 1615 -> 745 |
| 10 | statement order grids (marker blocks, the tail) | -> 690 |
| 11 | the 16-bit parameter is `s16`, its masks `(u16) arg0` | 1245 -> 690 |
| 12 | one word update as `w = F; w &= ~0xFC; w |= 0x10; F = w;` (a temporary of its own per marker block) and the flag update as `F = F \| 2;`; the second block's statements in the order a hill-climb over order and spelling found | 635 -> 180 -> 120 |
| 13 | the table slot's base staged between the index's halves: `ex = (s16) id; tab = T; p = &tab[ex];`, twice | 120 -> 60 -> 0 |

Each of those steps was found by hand and is now a library family (decomp-repair #1401, #1405, #1406, #1411):
`call_into_each_arm` (9), `arm_constants_to_head` (2), `local_pointer_web_split` (7), `shared_sum_local` (5),
`narrow_param_mask_cast` (11), `result_field_reread` (6), `rmw_compound_temp` (12), `table_base_stage` (13). Each was run on the source before its step
with the app's proposal path and scored with the judge: the child reaches the measured score.

Failed experiments that would otherwise be repeated:

- Typing every access (a struct of named fields for the record, the object and the marker) scored worse than casts for the
  record (more reordering); typing only the object's fields was better (4625 -> 4505).
- Permuting the six statements of a marker block (720 orders) with the single-expression word update never beat 24 rows; only
  the compound temporary form did, which is why the family offers the spelling and the pair.
- Table-index spellings (`&T[i]`, `T + i`, `(char *) T + (i << 2)`, a table pointer local) all compile to the same RTL here:
  the `sra`/`sll` placements stayed.

What is controlled evidence and what is one function:

All of it is one function. The steps' scores are measured with the same judge from the previous step's source; the
compiler explanations in the families' docstrings (jump.c cross-jumping, global.c's first-used-register pass, cse's
reassociation, local-alloc quantity tying) are hypotheses read from the source, not confirmed by a debugger.
