---
title: loop latch and hit arm shapes in a 435-instruction hit search
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags: a 435-instruction N64 function walking a
100-record pool for a record that overlaps the caller, with eight "tested, then set" cooldown globals in the loop and
a hit that returns the record. The per-function profile above; no profile change was needed.

The source change, and the measured result before and after (scores, the judge used): the machine draft was 8830. A
typed rewrite from the assembly (record struct, `u16` counter declared before the spilled radius, loop test with
`return NULL` inside a `for (;;)`, squares shifted in place) was 1915 (app's judge-worker). Then, each step checked:

- `if (g && (a || b || c)) { hit; return rec; }` as `if (g) { if (a) {...} else if (b) {...} else if (c) {...} }`:
  loop.c moves each copy to a barrier before the loop and jump2 merges them (the target's hit code sits among the
  code above the loop). 1915 -> 470. `or_arm_split`.
- the array the loop reads once held in a pointer local set before the loop: 470 -> 375. `loop_table_local`.
- the old value of the first two tested-then-set globals in a local of a block of its own: 375 -> 165. Their `%hi`
  registers' lifetimes grow in `-dL`'s list and loop.c moves both addresses, which the target rematerialises into
  `$t0`/`$t1`. `global_test_value_local`.
- `kind = rec->kind;` at the top of the body assigned inside the condition's last test: 165 -> 0.
  `condition_last_read_assign`.

Failed experiments that would otherwise be repeated: `-mfp32` changed nothing; swapping the two stores of a latch
(timer first) moved the address but the scheduler keeps the source order; a local `started = 1` inside the `if` moved
both addresses but turned `xori ,1` into `xor` with the hoisted 1; `break` at the loop's top test rotates the loop.

What is controlled evidence and what is one function: the four steps are one function. That the `%hi` movables decide
the reload registers is read from `-dL`; that the block's scope note is what lengthens the lifetime is a hypothesis.
