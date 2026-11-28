---
title: Commute a bounded signed row offset
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

A large MIPS function used a signed byte to select a row of eight unsigned
halfwords. The local `guard_row = row[c]` preserved the signed index and
scored 755 in the linked O1 diagnostic.

Writing the same address as `(u16 *)((c * 16) + (u32)row)` reduced the
score to 750; the app independently recorded that diagnostic. The byte index
keeps its sign through a bounded signed multiplication, then the 32-bit MIPS
address addition uses unsigned arithmetic. This spelling relies on the named
compiler and ABI. The reverse order `(u32)row + (c * 16)` tied at 755.

The generated child has the same result for valid row accesses, including
negative indexes; a later unsigned-index read was left unchanged. The result
is one linked function, and the cause in GCC's allocation is a hypothesis,
not controlled evidence from a compiler pass.
