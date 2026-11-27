---
title: a record spawner: the constant store below the second call and two stores moved together
kind: function
compiler: SN64 GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 107-instruction particle spawner: allocate a record, call a few leaf helpers, fill the record with a run of stores.
Scores are the app's judge on the whole function; the saved best stood at 2930, the match is 0.

- A typed record, the table read as words, the callee's `s16` result declared: 2930 -> 1560.
- Three same-valued byte stores as one chain `p->r = p->g = p->b = v;`: -> 1165, then statement order -> 1025.
- `store_adjacent_gather` (library): -> 615. It moved a constant store above the statement that holds the last call.
- The random local declared a halfword (`local_width` offers `unsigned short`): -> 390. The target's
  `move $v0,$s1; andi $v0,$v0,1` is the halfword local read once while the later test works in place. A word local
  with a cast at one use shared one `andi` for both reads.
- The constant store written below that call, in the middle of the later run, and the next-but-one pair of stores moved
  above the two before them, both at once: -> 0 (`sunk_store_pair_order`). Where the constant store had been, the
  candidate stored it before the `jal`; the target stores it after, in the multiply's latency.

Failed: writing the angle offset through a fresh local gave worse register allocation; an inline helper, a `u8` copy
and a copy-then-mask of the random value left the `andi` shared; eight places for the constant store alone scored
140 to 540 with the other pair order, none exact until the pair moved too.

Controlled: the family's child from the 390 source was judged 0 once. That the scheduler's order among independent
stores is what the two moves change is a hypothesis; one function.
