---
title: A pool scan counter stepped inside its test, and top-tested counted loops (SN GCC 2.8.1)
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-28
---

What the function (or case) looked like and the exact compiler and flags:

A 443-instruction N64 effect updater (an orbiting part that bursts into particles), SN GCC 2.8.1
`-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Its two counted loops keep `sltiu; beqz` at the head
and a `j` back, and the second one scans a particle pool for a free slot with a `u16` count.

The source change, and the measured result before and after (scores, the judge used):

The app's judge. The m2c draft stood at 5090. A rewrite from the assembly (a struct for the object, one for the
particle, plain `for` loops, one `u16 i` for both loops) scored 3465. Then:

- `while (1) { if (i >= n) break; ...; i++; }` for both outer loops, 175. `while (1) { if (i < n) {...} else break; }`
  (`loop_else_break`, one loop at a time) does the same; its all-loops child also rewrites the inner pool scan, which
  must stay a `while`.
- one counter per loop (`local_web_split`).
- `if (++k >= 501)` written as `k++; if (k >= 501)` (new: decomp-repair `preincrement_before_test`). The combined
  form gave `addiu $v0, $s7, 1; move $s7, $v0; andi $v0, $v0, 0xffff`, the split one the target's
  `addiu $s6, $s6, 1; andi $v0, $s6, 0xffff`.

The three together are exact. `prove_chain.py --families loop_else_break,local_web_split,preincrement_before_test`
goes 3465 -> 1085 -> 175 -> 35 -> 0 from the `for` source; without the new family it stops at 140.

Failed experiments that would otherwise be repeated:

Giving the m2c draft only the top-tested loop form made it worse (5090 -> 6490): its derived pool cursors and
untyped fields had to go first.

What is controlled evidence and what is one function:

One function. Why the combined increment goes through a temporary is a hypothesis, not read from compiler source.
