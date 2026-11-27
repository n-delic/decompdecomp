---
title: "Chain spawner: a zero test of a limit is the comparison with the cleared counter"
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: controlled
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 195-instruction N64 routine that clears two counters, tests three of them against three table bytes, then allocates six
linked records, filling each with a run of field stores. The target tests the second table byte (`bnez`) and later tests
it again (`bnel`) although the early branch seems to settle it. Saved best (machine draft): 4018.

The source change, and the measured result before and after (scores, the judge used):

The app's judge, each step on top of the one before:

1. one result local for the guarded calls, each record copied to its own name (`shared_result_scratch`): 4018 -> 2591;
2. `limit[1] == 0` as `counter1 >= limit[1]`, `counter1` stored 0 just above (`zeroed_field_compare`): -> 2371.
   m2c writes the folded zero test. From `limit == 0` cse learns the limit is zero and drops the later test; from
   `0 >= limit` it does not (the equivalence record is a hypothesis);
3. the other two comparisons written `limit <= counter` (`comparison_sides`): -> 1921;
4. in five of the record blocks, the `depth = parent->depth + 1` store above the mode and colour stores
   (`statement_slide`, `statement_slide_far`): 1651, 1381, 1111, 270, then 120 and 0 for the block that needs two slides.

Failed experiments that would otherwise be repeated:

Testing the second byte as `limit[1] != 0` in either nesting tried (early returns, one `&&` chain) loses the later test.
Five distinct link variables (one per record) score 560 in a typed rewrite where three rotating ones score 0; the
machine-draft route above needs no rotation. In that typed rewrite the sixth block's depth store also had to precede the
`mode = flag = 2` chain.

What is controlled evidence and what is one function:

The step scores are controlled judge runs. That the shape is general (per-kind counters against per-kind limits) is one
function. The match uses no scaffolding.
