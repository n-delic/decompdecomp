---
title: a stream cursor's saved old values written back as *field++
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A 69-instruction N64 audio sequence reader: a `do { } while` that counts a timer down and reads up to three bytes of a
command stream through a pointer kept in the voice record. The saved closer attempt (kept under this profile, the
function's own profile then being SN GCC 2.8.1) scored 250 with m2c's `p = F; F = p + 1; ...; v = *p;` at all three reads.

The source change, measured with the app's isolated judge (decomp-repair families, in the order `prove_chain.py` took):

- `cursor_field_postincrement_strip` (every site as `*F++`, then `scaffold_strip`): 250 -> 40.
- `statement_slide` (the 32-bit tick update above the 16-bit timer countdown): 40 -> 0.

The profile mattered first: a plain rewrite modelled on a matched sibling reader was exact under KMC at once.

Failed, measured on the same seed:

- One site alone as `*F++` is worse than the draft (the first 470, the second 815); the step has to be one child.
- All three sites with the search's identity inline kept: 150; with its word copy of the byte kept: 200. Both gone: 0
  after the statement order.
- The third read's sum as `F += *p++ + 2` instead of `F = *p++ + (v + 2)` was not needed once the sites were folded.
- `scaffold_strip` left the inline in place before this change: it did not know `__inline__`, did not unwrap an argument
  nested three parentheses deep, and read the `xFF` of `0xFF` as an operand, so no hex expression was ever substituted.

Controlled: the families' unit tests. One function: the scores above.
