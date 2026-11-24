---
title: store run order independent stores
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mfp64
evidence: controlled
date: 2026-11-24
---

Three N64 object constructors (a record from an allocation call, then a run of field stores through it; SN-N64 2.8.1
`-G0 -mips3 -O2 -mfp64`, 58-71 instructions) stood at 80 each in a saved best and reached 0 by statement order alone,
no other edit: `p->0x64 = 0xC8;` written above `p->0x98 = &handler;` (the m2c order has the address first) in two of
them, and in the third the last two statements of a four-statement run (`p->0x68 = 0xE;` and the constant store at
offset 0) swapped, a statement that reads `p->0x94` into a local sitting between them. The scheduler orders independent
insns by priority and among equal ones by statement order, and the allocator numbers pseudos by first use, so the order
decides which constant is loaded into which register and whether `li` comes before the `lui/addiu` of an address
(hypothesis from the three matches, not from the compiler source).

Why no family found it for the two m2c drafts (stalled at 80):
`statement_slide` reads an `M2C_FIELD` only when its type has no `*` inside, and every code address m2c stores is typed
`M2C_UNK **`, so that statement was "not a cell", a barrier nothing moves past; its nine children never had the constant
store above the address store. `independent_statement_swap` (which parses the third constructor's spelling and so already finds its swap) offers no child at all
on the first m2c draft (0 children; the cause was not traced in its code), `offset_store_order` wants a run it can put in
order of offsets, `member_store_hoist` handles `->` members only. No limit on distance, child cap or purity guard was the cause: the statements were not parsed.

Fix: decomp-repair `store_run_order` (family `store_run_order`) models stores and plain-local assignments through one
base in every spelling, offers each adjacent swap and each move to the front or back of a run (constants and addresses
first, at most 24), and `store_run_constant_local` offers a constant stored at two places as `k = C;` once. Measured
with the app's judge from the reverted starts: `prove_chain.py --families all` (decomprepair 0.5.1021, the pin on main) goes 80 -> 0 at step 1 on all three from the reverted starts (the m2c draft for the first two, the matched source with its last two block statements swapped for the third), the child being `store_run_order` for the first two and `independent_statement_swap` for the third (its own child ranks first there; `store_run_order` offers the same swap, direct call: 80 -> 0 as the fourth of seven children). Before the release (0.5.1018) the same runs stopped at 80 on the first two ("no child of 189 / 207 improves on 80") and reached 0 only on the third (`independent_statement_swap`, in the library since 0.5.871, so also in the app's 0.5.1006): the third constructor's stores are spelled `(((Rec *) ...)->v)`, which `independent_statement_swap` reads, so that one never needed this family; the two with the address store did.

Correction found afterwards: with `--combine 10 --children 400` (every child scored, 189 and 207) the 0.5.1018 library, and
the app's 0.5.1006 (the module is unchanged since), also reaches 0 on the first two starts at step 1, through
`identical_statement_comove`: the constant store `0x64 = 0xC8` appears twice in each function, and that family moves
the identical statement one place up in every run that has it, which here includes the harmless move of the second copy.
It sat behind the default 80-children cap of `prove_chain` (so "stalled at 80" above is the tool's cap, not a lack of
children), and it only fires because the constant is stored twice; `store_run_order` reaches the move whether or not the
statement repeats and ranks it first. Whether the Deep Permuter scored that child in its own runs was not checked (no
run was started).

A constant local was not needed to match the third constructor here: with the repeated `0x117` written literally, four of
the 24 orders of the four-statement run score 0 (the same as with a local `k`), so `store_run_constant_local` is a
variant the hand match used, not a measured requirement. From the 20 non-zero orders of that run a greedy chain of
adjacent swaps and front/back moves reaches 0 from 11; the other nine sit on a plateau (80 or 40) where every one-move
child ties, which needs `--combine` or the Deep Permuter's beam.

Evidence level: controlled for the three functions (judge scores); the false-positive load is measured over the 1130
matched functions of one N64 project with source: children offered on 268 (23.7 %), 1145 in all, none above the cap.

Not covered: the m2c draft of the third constructor (1170) needs the return type, the load into a local before the stores
and the split constant as well; this family only reorders what is already written that way.
