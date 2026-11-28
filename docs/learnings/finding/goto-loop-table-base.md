---
title: goto loop table base
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

An N64 state handler has a backward-goto loop. Its draft assigned a stable table address to the same local pointer
on each iteration, and cached an index across two calls. The target reloads that index after the calls.

Under `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, staging the call result and reloading the index afterward
restored the target frame size but changed the local linked score from 10,007 to 10,566. Hoisting the stable table
address to just before the loop then restored the target's stack offsets and improved that score to 9,930.

The corrected, hoisted source scored 25,314 under the function's currently saved `-O2` profile, so it was not
submitted. The alternate `-O1` profile remains diagnostic and has not been applied to the project.

This is one observation, not a verified match or a general allocator rule. A generated repair reproduced the hoist;
a compiled synthetic control checks the loop's behavior. Game source and data remain private.
