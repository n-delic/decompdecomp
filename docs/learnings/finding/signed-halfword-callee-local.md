---
title: signed halfword callee and reused local
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A MIPS function called a volume helper, then another helper, then passed the first result as a signed 16-bit argument. The helper's original SDK declaration returns `s16`; the target helper's final shift also sign-extends bit 15. The draft instead declared its return `s32` and used a fresh `s32` local for that result.

Changing the return declaration to `s16` and reusing an existing `s16` volume local together changed the exact linked judge score from 10 to 0: both sign-extension instructions then used the target registers. The 534-instruction target and candidate were byte-identical. Changing only the return declaration scored 725; reusing the local with the original declaration scored 10. The target callee and SDK support the signed return type; why the combination changes GCC's register choice is a hypothesis.

This is one exact match, not a controlled compiler regression. A remaining automation gap is to read the callee's proven return-width evidence and combine a declaration correction with a safe local reuse; neither change alone demonstrated the improvement here.
