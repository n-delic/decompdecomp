---
title: Guarded switch cases and chained shared tails
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 1,558-instruction N64 function had three adjacent eight-entry jump tables. Its draft used an `if (flag == 0)`
with the zero arm first, and two switch arms jumped through a chain of shared labels. The target used the opposite
block order and a guard of eight on a switch whose draft only named cases 0 through 4.

The exact app judge under `-G0 -mips3 -mgp32 -mfp32 -O2` measured these changes on one function:

- `if (flag == 0) { A } else { B }` -> `if (flag != 0) { B } else { A }`: 94,245 -> 54,270. Under a diagnostic
  `-G0 -mips3 -mgp32 -mfp32 -O1` profile the same change measured 80,200 -> 12,085. The existing
  `else_if_arm_swap` family offered the exact source.
- Add `case 7: break;` to the switch whose `sltiu` guards eight entries, while preserving its empty default:
  54,270 -> 54,120 at -O2 and 12,085 -> 11,935 at -O1. The older empty-case families only offered the next value,
  case 5; `guarded_empty_switch_case` now offers the last value supported by a direct table guard.
- Inline a label whose body ends in a goto to another labelled tail, then inline the newly exposed tail and the
  other shared switch tail. The first copy alone scored 54,655; the combined child scored 51,205 from 54,120 at
  -O2 and 9,910 from 11,935 at -O1. `goto_arm_tail_inline` now offers this bounded chain together.

The earlier m2c draft duplicated the compiler's NaN retry around `sqrtf`; `sqrtf_nan_fallback` removed it and scored
97,280 -> 94,245 at -O2. Removing an unrelated extra empty `case 8` alone changed the -O1 score from 12,085 to 13,505;
the target's first guard is eight, so that extra label remains a source issue to revisit with other changes.

These are observations on one function with the exact judge, not controlled compiler regressions or a match. The
synthetic family tests check the rewrites' conditions and behavior-preserving source shapes, not the game's bytes.
The -O1 scores are compiler experiments; the function's recorded profile remains -O2.
