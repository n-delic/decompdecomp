---
title: frame fence strip wrapper
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

What the function looked like: a 6-instruction wrapper (`(arg0 & 0xFF) * 0xC` index, six-argument call) whose saved best carried search scaffolding: `(void) (&temp_v0);` and a `loop_goto_1:` block followed by `if (0) goto loop_goto_1;`. Score 104, frame 8 bytes too big. KMC GCC 2.7.2, -O2.

The source change and result: strip both (plain `temp_v0 = ...;`). Judge 104 -> 0. The address fence alone also gives 0; stripping only the dead goto and label stays 104. The `(void)&x` makes the local an addressable frame slot, which grows the frame.

Failed experiments: none beyond the goto-only strip. Library family: decomp-repair `frame_fence_strip` (children `frame_fence_strip`, `_address`, `_goto`).

Evidence: one function; reproduced from the pre-match saved source with the app's judge.
