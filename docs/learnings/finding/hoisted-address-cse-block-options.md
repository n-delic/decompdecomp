---
title: A global address kept in a saved register that the target builds at each call
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags: a 146-instruction N64 dispatcher (a switch
whose first arm passes `&queue` to two calls) under gcc-2.7.2-kmc `-G0 -mips3 -mgp32 -mfp32 -O2`. cc1 kept the address
in `$s2` and copied it into `$a0`; the target has `lui/addiu $a0` before each call.

The source change, and the measured result before and after (scores, the judge used): plain C (struct-typed pointers, a
local pointer per arm, `default: goto fail;`) scored 605 at `-O2`, 605 with either `-fno-cse-follow-jumps` or
`-fno-cse-skip-blocks` alone and 0 with both (the app's own judge).

Failed experiments that would otherwise be repeated: other spellings of the arm (goto, `do {} while (0)`, a switch on
the result), a `volatile` or array declaration of the global. An identical-arm `if (global) {..} else {..}` around the
first call also scored 0 at `-O2`; it was not used, being a dead branch.

What is controlled evidence and what is one function: one function. The probe (`kmc_cse_hoist_probe`) and flag search
now offer both options together; whether the original build used these flags is a hypothesis.
