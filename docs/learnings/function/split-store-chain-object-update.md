---
title: Chained member stores and a read-back into an int local
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A small N64 object update that grows three halfword members, writes three colour bytes, calls two helpers and keeps a
frame counter. The saved best (1220-1255) was a searched m2c draft with temporaries and one-shot fences.

What matched (score 0 in the app's judge):

- the three halfword members assigned as one chain, `p->b = p->c = p->a = p->c + k;`, then read back into an **`int`**
  local (`size = p->a;` gives the target's `lh` into a saved register). With a halfword local the compiler forwards the
  stored register instead (`move; sll; sra`, 650);
- the three bytes as one chain of the same expression; the phase as `p->phase += k;`;
- `(p->time >> 2) % 5`, which the draft spelled as a subtraction of a division by 20 (`shifted_dividend_remainder`);
- no fences, and the callee's second and third arguments (one literal, set once and copied).

From the saved best, `shifted_dividend_remainder`, then `narrow_widening`, `target_call_arity` and `split_store_chain`
together reached 380 in the isolated judge. The last step was a member read after a call where the target stores the
read-back local; that changes behaviour, so no rewrite offers it.

Failed: `volatile` on the member or the read (400-1830), a halfword local with other chain spellings (650-3365).

One function, not a controlled compiler experiment.
