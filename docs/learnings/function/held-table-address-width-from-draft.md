---
title: a word read through a held table address stops the draft: the target's lbu names the width
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

A 153-instruction leaf copying a four-float row into three records, each picked by a halfword id (three identical
blocks). Under the project's `-O2` the saved best was 675 after weeks of attempts; plain C with record arrays
(a stride-100 record, 16-byte float rows, `u8 t[][16]`) was 0 under `-O1` at the first compile (judged by the app).

From the machine draft under `-O1`, `prove_chain.py --families all` stopped at 2595: m2c read a table it knew nothing
about as a word (`*(s32 *)(... + D + ...)`), and the target's `lbu 0($v0)` comes through a register holding the
address, so no `%lo` load named the width. With decomp-repair `asm_address_read_width` the chain is
unknown_symbol_offsets 3990, m2c_refinement 2775, asm_address_read_width 1755, linear_subscript_view 210,
merge_disjoint_temporaries 0.

The target's prologue has no saved registers (a leaf), so no prologue tell pointed at `-O1`. One function.
