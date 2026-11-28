---
title: Guarded compare tree as a switch
kind: finding
compiler: GCC 2.7.2 KMC, -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-28
---

A 546-instruction N64 audio dispatcher had an inner controller compare tree with five discrete values. Two leaves computed scalar locals before returning early for values outside their case. The source used a structured event record and the recovered outer status switch.

Rewriting the inner tree as a switch preserved each local computation in its case and returned on the default path. The live ROM judge improved from 6261 to 4496. The original case order and several other orderings scored worse; placing the middle-value case between the low and high cases produced 4496. The `guarded_compare_switch` repair emits that candidate in both the draft and local lanes. This is one observed near-match, not a controlled exact match.

Making the event or voice pointer volatile to force stack spills worsened other nearby candidates. Converting voice loops to goto-based loops also worsened them; simple `for (;;)` equivalents tied. These observations are separate from the switch result and do not establish a general compiler rule.
