---
title: A voice allocator's search loop written as a for, and a reused local's value in the store chain
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

A 106-instruction N64 voice allocator: take a free voice slot (return its serial number) or steal the lowest-priority
one. Its saved best was an m2c draft kept under SN GCC 2.8.1 (2380). The same source scored 220 under KMC GCC 2.7.2,
the profile of most of its matched neighbours: compare profiles before editing C.

The source change, and the measured result before and after (scores, the judge used):

App judge, in this order from the saved best:

| step | family | score |
| :-- | :-- | --: |
| start | | 220 |
| the guard's early `return` | `guard_tail_return_hoist` | 105 |
| table rows as an array member (`addu` operand order) | `member_array_rows` | 75 |
| the guarded `for (;;)` with a `goto` exit as `for (i = 4, p = base, q = p; i < n; ...)` | `guarded_exit_for_loop` | 15 |
| the callee's one real parameter | `callee_arity_trim` | 15 |
| the reused local's second value written in its store chain | `dead_value_store_chain` | 0 |

A clean rewrite with typed structs, one `for` and `best->f4 = best->f80 = ...` was also exact.

Failed experiments that would otherwise be repeated:

- Moving `i = 4` after the pointer load to place its `li`: worse (145 against 105).
- Splitting the reused local into two locals, with or without a temporary: 95; a temporary shared with the loop's
  arm: 155. Only no local at all (the value in the chain) was exact.
- The store chain without the callee trim: 95.

What is controlled evidence and what is one function:

Each score is the judge's, on one function. Why the `for` header moves the load order and why the local's second
life moves the first one's register are hypotheses, not read in the compiler source.
