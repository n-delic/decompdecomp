---
title: code symbol declared as data
kind: finding
compiler: SN64 sn-n64-281 -G8 -mips2 -O2
evidence: observation
date: 2026-11-24
---

What the function looked like: a 30-instruction N64 constructor (SN compiler `sn-n64-281`, `-G8 -mips2 -O2`) that
allocates an object, stores -1, a callback address, 0, 3 and a float global into its fields and calls a registration
routine. m2c declares the callback `extern M2C_UNK func_XXXXXXXX;` (a one-word variable) because the game only takes its
address, and writes `M2C_FIELD(obj, void (**)(void), 0x9C) = &func_XXXXXXXX;`.

The source change, and the result: declaring it `extern void func_XXXXXXXX(void);` and leaving every use as it was took
the judge 80 -> 0. With the data declaration the `lui %hi(callback)` came after the float global's `lui $at` / `lwc1`;
as a function it comes before it, as in the target. Nothing but the declaration differs. The decomp-repair family
`code_symbol_declaration` (`code_symbol_function`, and `code_symbol_byte_offset` for `&name + N` written
`((s8 *)&name + N)`, the form an earlier match of an address-plus-offset function symbol used) now proposes it for a
`func_XXXXXXXX` name (or one the header/callees call) declared as plain data and used only as an address. Hypothesis, not
read from the compiler source: a function's address is scheduled with the surrounding expression, a data address where
the constant is first formed.

Reproduction: from the saved match with the declaration reverted, `prove_chain --families all` stayed at 80 (no family
touched a declaration's kind); with the family, one step 80 -> 0. From the raw m2c draft (330) the greedy chain is stuck
at 120 (the declaration alone does not improve the draft: it has `(f32)` casts and a field type too), and
`--combine 10` finds `code_symbol_function+field_struct_view` -> 0; from the draft with the declaration fixed,
`float_literal_global` reaches 0.

Failed experiments that would otherwise be repeated (by hand, same function): 720 orders of the six stores, fenced
do-while variants, pointer and float locals, the plain float cast, `&` and cast address spellings of the data
declaration: all 80, one 385.

Evidence: one function (observation); the family's tests are controlled for the rewrite, not for the compiler fact. Run
over 1,070 matched sources of the same game it proposes children for 212 (1 to 7 each, mostly 1 to 3): most of those
match with the data declaration, so the family is a cheap candidate, not a rule.
