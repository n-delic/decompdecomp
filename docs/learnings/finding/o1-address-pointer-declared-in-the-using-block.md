---
title: o1 address pointer declared in the using block
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 194-instruction N64 save-flag checker (no parameters; the profile was `-O2`, and no spelling reached it) matched under
`-O1`. Its prologue saves `$ra`, `$s2`, `$s1`, `$s0` and the accumulator's zero fills the first branch's delay slot.

The source change, measured with the app's judge:

- Neighbouring globals the target reaches from one register (`lh -0x16($s1)` beside `lh 0($s1)`, `lh 4($s1)`) were one
  record: a struct whose member at that offset is an `s16` array. Reads of the other globals inside the same record's
  span (`lui; lhu` of record + 6) were that record too.
- With `Rec *rec = &record;` declared at the top of the outer `if`, the pointer's `lui`/`addiu` came before the array
  address the next `if` computes: 420. Declared at the start of the inner `if` that uses it: 0.

`address_local_scope_sink_uses` (decomp-repair `address_local_scope_sink`) offers that move and reaches 0 from the 420
source. A hand-written 175-instruction neighbour under the same profile (a record array whose first member points to a
data record, every field reached through the index again) matched on its first compile.

What is controlled evidence and what is one function: the 420 -> 0 step is one function. That `-O1` places an
address set by its declaration's block is a hypothesis, not read in the compiler source.
