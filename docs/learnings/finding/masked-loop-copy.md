---
title: masked loop copy
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-27
---

A function masks a byte, scales it by 16, calls a helper, and compares the original masked value during a linked-list traversal. The target masks into a saved register and later copies it to the loop's comparison register.

Changing the masked local from `u8` to `u32` and copying it to a separate `s32` before the loop changed the exact judge score from 210 to 10. A fresh local and an existing otherwise-unused local each achieved 10. Both candidates contained 534 instructions.

Widening alone scored 230: it made the target mask but changed several registers. Copying to a reused byte local scored 440, and other reused integer locals scored 40 to 850. The compiler's allocation rationale is a hypothesis, not established from its source.

These scores are observations on one function. The repair's synthetic tests check its source and target guards; they are not a controlled compiler regression.
