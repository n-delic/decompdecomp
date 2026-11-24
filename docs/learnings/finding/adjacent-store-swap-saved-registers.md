---
title: two adjacent final stores of locals to unrelated places, written in the other order, exchange the registers their values take
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp64
evidence: controlled
date: 2026-11-24
---

What the function looked like: functions that end with stores of two values they hold in locals, to two global words or
two fields of one record (`G1 = a; G2 = b;`): a 41-instruction N64 object constructor ending in three field stores from
saved registers, and four 20- to 90-instruction setters and initialisers. The stores have no dependence, so the order is
a free choice of the original's author, and it decides which local's live range ends first and so which register each
value takes (local-alloc and global-alloc rank a pseudo by its references over its lifetime).

The source change, and the measured result before and after (scores, the project judge): controlled, because the project
has no unmatched function that needs it: the matched source of five functions with such a pair, with the pair written the
other way round (seven sources). Each swap scored 20, 50, 0, 40, 60, 40 and 40 (the 0 is a pair whose registers do not
change), and `adjacent_store_swap`, gated on the target, gave the matched source back in six of the seven. The seventh
is a pair of float stores through `swc1`, whose registers the gate does not read. The same edit changes no instruction
count, only the registers and the order.
Library: decomp-repair `adjacent_store_swap`: the trailing run of plain stores of distinct locals to named globals or to
fields of one base, each adjacent pair a child (at most 4); the gate is that the target stores the two in the other order
from the source's (any registers), or stores two saved registers with the first one stored in the higher-numbered one.

Failed experiments that would otherwise be repeated: requiring saved registers in the gate (it admitted two of the
seven; the setters store argument registers and the order alone says the source was swapped); swapping a global with a
field of a pointer (they may be one object).

What is controlled evidence and what is one function: controlled only: no function of the project's unmatched set was
helped by it (it fires on one saved draft, a pair of stores whose swap the matched source does not use), and the existing
`store_run_order` already proposes the same text for many pairs, so a child can carry that family's label. The gate
cannot tell a needed swap from one the scheduler would make anyway; the judge decides every child. Not covered: pairs
that are not at the very end of the function, stores whose values are expressions, and stores to array elements.
