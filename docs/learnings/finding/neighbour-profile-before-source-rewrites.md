---
title: neighbour profile before source rewrites
kind: finding
compiler: gcc-2.7.2-kmc
evidence: observation
date: 2026-11-26
---

An N64 project built `-G0 -mips3 -mgp32 -mfp32 -O2` by default under gcc-2.7.2-kmc, where several hundred functions are
matched per function under the same flags with `-O1`. Three open functions (45, 50 and 51 instructions) had 6 to 8 of
their 8 nearest matched neighbours on `-O1`. Their saved bests (130, 445, 445) were tuned for `-O2` with `do { } while (0)`
fences, a padding array and goto chains.

All three were exact under `-O1` from plain C written by hand:

- Two predicates (`if (x == K) return 0; if (x == table) return 1; if (a < 5 || b < 10 || c == K) return 1; return 0;`)
  scored 0 as written (one was first submitted with an unneeded goto).
- A bit rotation (two `for` loops over a `u8` count) scored 15 with `bit = (v & top) != 0;` and 0 with
  `bit = 0; if (v & top) { bit = 1; }`. The library's `temporary_hoist_block` makes that exact from the 15 in one step.

Failed experiments:

- The saved bests judged under `-O1` scored the same or worse (445 -> 445, 130 -> 770), and the machine drafts
  2200 and 1630, so the profile probe had nothing to report and the briefs said nothing.
- `scaffold_strip` and `clean_rewrite` forms of the saved bests under `-O1` stayed between 470 and 840.
- About 3,000 hand-grid candidates under `-O2` on the rotation never went below 210.

The change: the brief of an open ROM function now names the profile most matched neighbours were built with, when it is
not the function's own (`profile_probe.neighbour_profile_hint`), whatever the saved C scores under it.

Evidence level: one project, three functions.
