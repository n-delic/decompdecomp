---
title: switch labels from jump tables fno thread jumps
kind: function
compiler: gcc-2.7.2-kmc -O1 -fno-thread-jumps
evidence: observation
date: 2026-11-26
---

A 147-instruction N64 state routine with two 15-way jump tables (gcc 2.7.2 KMC). Saved best 1375 after many agents.

- Rebuild each switch from its whole table: labels grouped by destination, the arms in destination order, including an
  arm that only shares another's tail (`obj->state = 1; break;`). With only the four distinct cases written, the score
  is 1 with linked jump-table rows (or 10 with the next point).
- The one remaining row (a branch over a reload lands on the later test, not past it) is the jump threader:
  `-G0 -mips3 -mgp32 -mfp32 -O1 -fno-thread-jumps` is exact, `-O1` alone is 10. A per-function profile.
- The machine draft had no `case` labels at all: `m2c_types._stray_labels` took a switch whose brace is followed by
  m2c's `/* switch 1 */` comment for a plain block and removed every label (45 of 93 drafts with a switch in one
  game). Fixed with DRAFT_REVISION 30, so drafts are made again.

One function; the draft bug is controlled by `tests/test_m2c_types.py`.
