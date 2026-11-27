---
title: A union split that reused a local's name did not compile, and a profile change after the last pass left a two-step match
kind: function
compiler: sn-n64-281
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: a 119-instruction N64 object update,
`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Its saved best (160) had a
`union { void *pointer; unsigned int value; } __repair_phase_storage;` beside an unrelated local already named
`__repair_phase_storage_value`, a `do { } while (0)` fence and a byte colour staged through a wide local
(`w = (u8)(E); b = w;`).

The source change, and the measured result before and after (scores, the judge used): `phase_storage_split` named the
member's local `NAME_member` without looking, so its child, `scaffold_strip`'s child and `scaffold_strip.plain` declared
`__repair_phase_storage_value` twice and did not compile. With the member's local numbered when the name is taken
(decomp-repair #1993), the plain form scores 100 and `spill_slot_copy_collapse` on it scores 0 (isolated judge; the
app judged the equivalent source 0). Without the fix, `prove_chain` from the saved best also reached 0:
`spill_slot_copy_collapse` (160 -> 60), then `explicit_loop_backedge` (60 -> 0).

Failed experiments that would otherwise be repeated: under the project's default `gcc-2.7.2-kmc -O2` the same source
scores 240 and `spill_slot_copy_collapse` leaves it at 240. The last permuter pass on the function ran at that score,
before the profile changed; a near-miss that is closer under a newly applied profile is worth a fresh
`prove_chain` before hand work.

What is controlled evidence and what is one function: the name collision is a controlled regression test in
decomp-repair; the scores are one function.
