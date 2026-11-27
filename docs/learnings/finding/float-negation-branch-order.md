---
title: Float negation branch initialization order
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

In one large N64 function, two nonvolatile float locals used this branch:

```c
v = x;
if (!(x > 0.0f)) { v = -x; }
```

Writing `v = -x; if (x > 0.0f) { v = x; }` instead changed the register and branch layout. With the exact judge
under KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O1`, the two independent changes scored 5,705 and 5,880 from
5,970, and together scored 5,615. Under the function's recorded `-O2` profile the second scored 49,170 from
49,265 and was saved; the combined child scored 49,305. The `float_negation_branch_order` repair offers each
site and all sites together when the target contains the relevant float comparison and negation.

An earlier positive-first `if` / `else` spelling scored 10,040 from 9,910 on the preceding candidate and did not
help. A controlled source test checks the transformation's values for signed zero and quiet NaN, along with
ordinary positive and negative values; it does not check floating-point exception or trap behavior. The score
measurements are observations on one function, not a match or controlled compiler regression. The -O1 results
were retained as compiler trials; the recorded function profile remained -O2.
