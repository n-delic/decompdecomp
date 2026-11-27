---
title: A skipped parameter and a goto into a later arm's tail in a machine draft
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2
evidence: verified
date: 2026-11-27
---

A 59-instruction N64 image-header loader (`sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`)
whose machine draft scored 1054 in the app's judge.

Source changes, each measured with the app's judge from the draft:

- Two exclusive cases ending the same way were drafted as a `goto` from the first `if` (inside a `do { } while (0)`)
  into the second one's tail, with the default of the returned value assigned to a local nothing reads. Writing each
  arm with the tail and an `else` gave 605; also giving the returned local that default before the first call gave
  350 (`later_arm_tail_else`).
- The draft's parameters were named `arg1`..`arg5`: the first was left out because nothing reads it. The first callee
  reads `$a0` and `$a1`. Adding the parameter and passing `(arg0, arg1)` gave 85 (`skipped_register_parameter`).
  Passing one argument gave 545, three or four 245.
- The two remaining `do { } while (0)` scopes opened: 0 (`fence_open`).

Failed on the way: writing the two cases as a `switch` (1150 on a clean rewrite); assigning the default after the first
call instead of before it (the result then does not take a saved register: 1054 -> 420 on a clean rewrite only with
the assignment ahead of the call).

What is controlled evidence and what is one function: the chain is one function, verified by the judge. The rule that a
gap in the `argN` numbering means an unread parameter is how m2c names parameters; the library tests are homemade.
