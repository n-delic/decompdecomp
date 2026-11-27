---
title: m2c frame union and stepped table reads in a point-in-triangle test
kind: function
compiler: sn-n64-281
evidence: observation
date: 2026-11-27
---

A 149-instruction N64 leaf-like test (three edge constants of one triangle, then three points of another against them),
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. The saved best (2004) was an m2c draft whose
frame was one union with a view per slot, and whose loop read the point table through a `char *` cursor and through
`*(&view + var_t1)`, a byte offset stepped by 8.

Edit list from the saved best, measured with the app's judge:

1. Frame union written as one array per run of contiguous views (`flags[3]`, `c[3]`, `dx[3]`, `dy[3]`, `p[6]`):
   2004 -> 2004. As scalars instead of arrays: 13300 (the target stores every one of them).
2. The two stepped reads written by the counter, `p[i * 2]` and `p[i * 2 + 1]`: with step 1, 585. The compiler then
   makes the target's own pair: one pointer stepped by 8 for the first, the array's address held apart plus an offset
   starting at 4 for the second.
3. One branch polarity: 85.
4. One local per argument register (`temp_a2`, `_2`, `_3`, `_4` as one, and `temp_a1`'s likewise), the third edge's
   `s16` temporary included: 0. Merging the `temp_v0`/`temp_v1` runs as well: 30. Leaving the `s16` temporary apart: 30.

Library: `frame_view_locals` (runs child), `stepped_array_reads`, `branch_polarity`, `merge_disjoint_temporaries`
(decomp-repair pull request 2180); `stepped_array_reads` offers steps 1 and 2 as one child.

A clean rewrite also matched: `s32 p[6]` read as `p[i * 2]` / `p[i * 2 + 1]` (120) and the flags written through a
pointer set before the loop, `s32 *f = flags; ... *f |= ...; f++` (0).

Failed experiments that would otherwise be repeated: a point record `p[i].x`, `p[i].y` or `s32 p[3][2]` (1329: one
pointer reads both members); `(p + 1)[i * 2]` for the second read (610); `walk_counter_for`'s `for` loop with the
store cursor replaced too (650).

One function; nothing here was confirmed in the compiler's source.
