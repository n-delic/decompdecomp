---
title: four m2c shapes that KMC GCC 2.7.2 -O2 rejects, and the decomp-repair families that undo them
kind: finding
compiler: KMC GCC 2.7.2 -O2 (-G0 -mips3 -mgp32 -mfp32)
evidence: verified
date: 2026-11-26
---

Four small N64 functions (4 to 30 instructions) that an agent matched by hand from their saved best. Each is now a
decomp-repair family (decomprepair 0.6.113), and `prove_chain.py --families all` takes the pre-match source to 0 in one
step (the judge's score, nothing else changed):

- `narrow_table_byte_offset` (150 -> 0): a `u16` table of 4-byte records. m2c wrote `*(&sym + ((i & 0xFFFF) * 4))`; `+`
  on a `u16 *` scales by 2, so the compiler shifted by 3 where the target shifts by 2. Fix: the index is a byte count,
  `*(u16 *)((u8 *)&sym + ((i & 0xFFFF) << 2))`. Worked on the goto-ladder form the draft passes had left.
- `masked_copy_counter_merge` (200 -> 0): a `u8` counter m2c split into a word `var_a0 += 1` and a masked copy
  `var_v0 = var_a0 & 0xFF` read by the loop. One `u8` counter (`var_v0 += 1`) is exact; the `do { do {} while (...) }
  while (0)` wrapper and `0 & 0xFF` init did not matter, and neither did `char` versus `u8` extern arrays.
- `flag_table_byte_read` (200 -> 0): `*(s32 *)(table + i) != 0` where the target is `lbu; beqz`: `*(u8 *)` there.
- `list_walk_ladder_unwrap` (175 -> 0): an m2c break/fence ladder around a list find (result local reset to NULL each
  step) is the plain `do { if (key == arg0) return p; p = next; } while (p)`. `M2C_FIELD` accessors are fine; no struct
  type is needed.

Failed or not needed: none of the pre-existing families (253, 201, 53 and 168 children respectively, all families)
improved any of the four.

Evidence: one function each, verified by the app's judge (fast_judge, saved matches untouched). The synthetic tests are
in decomp-repair `tests/test_micromachines_families.py`.
