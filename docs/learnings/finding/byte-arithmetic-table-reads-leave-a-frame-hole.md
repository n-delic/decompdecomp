---
title: a table read as bytes plus an index leaves an unread 8-byte frame slot and a hoisted address
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: controlled
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags:

A 445-instruction N64 target picker (`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`) whose saved best matched every
instruction of its first half and still differed in the frame: `addiu $sp,$sp,-0xa0` against the target's `-0x98`, nine
saved registers and three doubles all eight bytes off, spilled values at the target's own offsets and nothing at the top.
Its tables were read as bytes plus an index (`FIELD(table, s32, (e & 0xFFFF) * 24)`, `table[i * 24]` on a byte array). The same
cc1, run by hand on small functions, gives two separate effects of that spelling:

* Two straight-line reads of one symbol (`info + i * 24`, a call between them or not) leave a stale pseudo. cse1 makes the
  second `(symbol_ref)` load a copy of the first one's register, so combine merges the first read's address sum into the
  `mem` while the symbol's own set is kept for the later read (`newi2pat`, combine.c), and the "zero its use count"
  step is skipped for the sum. The sum has references (`.lreg`: `used 2 times`), no insn and no register class
  (`ST_REGS or none`); `alter_reg` (reload1.c) gives a pseudo with `reg_n_refs > 0`, no hard register and no equivalence a
  slot, last in pseudo order. The slot is 8 bytes and nothing reads it (`.frame ... vars= 56`, 48 in the target). It
  appears for any two reads of one symbol (`ST=2` for two tables), and not for two different symbols, one read, or a
  `volatile` read. A struct-array access (`info[i].a`) never makes the sum: expand writes `symbol + index` as one address.
* A byte table indexed in a loop inside another loop (`table[i * 24]`, `i` a short counter) has its `la` hoisted by
  loop.c out of both loops into a saved register the target does not spend, and the access becomes `lbu 0($v1)` where the
  target has `lui $v0,%hi(table); addu; lbu %lo(table)($v0)` (combine only folds the symbol into the address when it is
  in the access's own expression). `extern u8 table[][24]; table[i][0]` is that expression.

The source change, and the measured result before and after (scores, the judge used; standalone judge):

| edit | score |
| --- | --- |
| saved best with every table as bytes | 2028 (frame `-0xa0`) |
| the loop's table as `u8 table[][24]` / `table[i][0]` | 1043 |
| the three straight-line tables as one-field records (`Rec12a D_800BF688[]`, `.f0`) | 1015, frame `-0x98` |

decomprepair `record_slot_rows` writes every such table as typed rows, one child per table and one with all of them (each of
the three straight-line tables alone does not move the frame: it needs the whole set that the two reads of one symbol
share). `record_slot_table` (older) covers only `D_` names and `*(T *)(D + e * K)`.

Failed experiments that would otherwise be repeated:

Reading the first access through a local, a different index form (`((int *)info)[i * 6]`, unsigned or short index), an
`if` or a `do { } while (0)` between the reads, one table pointer in a local: the stale pseudo stays (cse still shares the
symbol). A real loop between the two reads breaks the sharing (no stale pseudo in the toy) but is not the target's code.
Removing a spilled local to cancel the slot changes the offsets of the others (each spill takes an 8-byte slot in pseudo
order, so only the stale one at the top may be added without moving them).

What is controlled evidence and what is one function:

The stale slot (two reads of one symbol as bytes: `vars=` +8; one record: none) and the hoist are controlled on toy
functions with the same cc1 (docs above). The measured scores are one function. The mechanism text (combine's
`newi2pat` skipping the zeroing of the dead sum's count) is read from combine.c and is a hypothesis; the frame figures are the evidence.
