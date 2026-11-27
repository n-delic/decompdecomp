---
title: Envelope stepper: switch arm order and a float's two-step operands
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-27
---

A 119-instruction N64 audio leaf: a release test, then `switch (phase)` over a u8 with cases 1, 2, 3 (empty) and 4, each
computing an envelope volume from a float rate. Its saved best was under `sn-n64-281` (6895) with search scaffolding.

The match: plain C with a typed channel struct under KMC GCC 2.7.2 `-O2` scored 0 at the first judged variant (saved for
this function only). The neighbours' profile and the even-only float registers named the compiler.

The source change, from the m2c draft at 1765 under that profile, with library children and the exact judge:
`case_guard_arm_swap` 1585 (a case's `if (c) { A; case 3: return; } REST` written `if (!c) { REST } A; case 3: return;`),
`fence_open` 1285, `compound_pair_swap` 1270 (`v = (f32) n; v *= rate;` with the operands exchanged),
`case_guard_arm_swap` 475, `case_temporary_split` 245, `local_web_split` 0. From the saved best the same families
reached 890.

Failed experiments: no single child of eight operand and temporary families (31 children) improved the 15-point state
(`$f0`/`$f2` exchanged in three rows). `v = n * rate;` in that order stayed at 15; `v = rate * n;` was 0.

What is controlled evidence and what is one function: the scores are exact-judge results on this one function. Why
the operand order moves the float registers is a hypothesis, not read from the compiler's source.
