---
title: A symbol pointer set inside a one-shot wrapper, and a union's second pointer phase, keep a loop-bounded sound setup routine at 630
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 96-instruction N64 routine: one loop that allocates four sound slots and fills each in, one more allocation after
it, and a second arm with a loop bounded by a global it has just set. The saved best (630, 62 attempts) was an m2c
draft with search scaffolding.

A rewrite from the assembly matched on its first compile: a typed voice record, one pointer local for every
allocation, a `u16` counter and plain `for (i = 0; i < D_bound; i++)` loops.

Reduced to edits of the saved best (the app's judge, `fast_judge.py`):

- The loop bound was read through `__base = &D_bound;` standing alone in `do { } while (0)`. The pointer holds the
  full address in a saved register; naming the global leaves only the `lui` half there, as the target does.
  570 -> 60 on the scaffolding-stripped form, 630 -> 120 on the saved best. `symbol_base_unwrap` skipped it because
  the assignment did not start its line.
- The allocation after the loop went into a union's pointer member (then copied to `temp`). Writing it into the
  loop's own pointer local: 60 -> 0. The copy to `temp` can stay. Splitting the union alone tied (120 -> 120), so a
  greedy search never reached the reuse; `union_pointer_reuse` offers both as one child.
- The remaining one-shot wrappers around the counter then cost 60 (`fence_open`).

With both changes a greedy `prove_chain.py` run reaches 0 from the saved best: 630 -> 120 (`symbol_base_unwrap`) ->
60 (`fence_open`) -> 0 (`union_pointer_reuse`); from the stripped form 570 -> 60 -> 0 (`pointer_local_reuse`).

Controlled: the two edits on this function's own saved sources. One function; nothing here says every wrapped
pointer or union should go.
