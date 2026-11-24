---
title: a field used twice, read twice after a store to its neighbour, is a register copy in the target (reload_cse)
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp64
evidence: verified
date: 2026-11-24
---

What the function looked like: a 52-instruction N64 object updater with one branch whose body reads a record's field
once (m2c's `temp = F(0x80)`), bumps a counter in the neighbouring field (`F(0x7C) = F(0x7C) + 1`), stores the value into
a third field (`F(0x64) = temp`) and stores it back reduced (`F(0x80) = temp - 5`). The target has `lw $a0, 0x80(base)`
followed at once by `move $v1, $a0`: a register copy of a just-loaded field.

The source change, and the measured result before and after (scores, the project judge): the developer read the field
twice, the second time after the counter's store, and used each read once

    a = F(0x7C);  t = F(0x80);  F(0x7C) = a + 1;  u = F(0x80);  u -= 5;  F(0x64) = t;  F(0x80) = u;

GCC 2.8's reload_cse turns the second load of an address a register still holds into a copy, which is where the `move`
comes from; one read with two uses has the plain `lw` and one register for both. The single edit took the m2c draft
625 -> 555; the rest of the function (an override store split, two statement slides) took it to 0 with
`prove_chain --families all`: `second_field_read` 625 -> 555, `override_store_split` 245, `statement_slide` 10,
`statement_slide` 0.
Library: decomp-repair `second_field_read`, children `second_field_read` (the read just after the intervening store) and
`second_field_read_late` (just before the use), gated on the target's load-then-copy of that offset.

Failed experiments that would otherwise be repeated: applied last, to a source other families had already brought to
185, the same edit scored 360 and 500 (one child per use) and was dropped, so the edit belongs early in the chain, on the
draft; with `--families all` and the children capped at 60 the greedy chain took `override_store_split` first (305) and
never offered it again.

What is controlled evidence and what is one function: one function, verified by the project judge (0), through a
four-step chain; the second read alone is a first step, not the match. The gate fired on 46 of 2,206 targets of a
project and the family on one function's draft (nothing judged), and on no saved best or matched source.
Not covered: a field of a record named through a pointer other than the base of the stores in between, and a local
used by more than four later statements or inside a loop.
