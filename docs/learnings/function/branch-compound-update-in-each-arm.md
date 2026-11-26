---
title: branch compound update in each arm
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-26
---

A 63-instruction N64 per-entry update ending in an `if`/`else` that moves one float field toward 1.0, once scaled by
0.5. `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1` (the project default is `-O2`; per-function profile).

At 25 only the product's destination differed in both arms (`$f2` where the target has `$f0`). The arms computed into
two locals and one store after the `if` added them:

    fv = (1.0f - P) * A [* 0.5f]; ft = P;   /* in each arm */
    P = fv + ft;                            /* after the if */

Each arm updating the field itself was exact (the app's judge, 25 to 0):

    P += (1.0f - P) * A [* 0.5f];

One arm written so and the other left with the temporaries (its own store) was also 0, as were `P = E + P` and either
operand order of the product.

Failed: compound assignments on the temporary (`fv = 1.0f - P; fv *= A;`, 55); the store after the `if` reading the field
again (`P = fv + P`, `P += fv`, 145); copying the store into the arms with the locals kept (525 to 590).

The library already reaches it: `join_tail_arm_split` takes the 25 to 0 in one step (`prove_chain.py`). The near-miss
had waited as a compiler trial because nothing runs the repairs on another profile's C; `haiku.py done` now does.

One function; the compiler reason (the temporaries' lifetimes in allocation) is a hypothesis, not read from the source.
