---
title: signed table pointer offset
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-28
---

An N64 state handler reads a byte through a stable pointer to a table and uses the result as an additional table
offset. The draft's pointer was `u8 *`, while the target's direct load of that same table was `lb`, a signed byte.

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1`, writing the read as `*(s8 *)(base + index)` reduced the local
linked score from 10,202 to 10,007. This corrected values with the high bit set. The app's profile remains `-O2`;
the result is an alternate-profile diagnostic, not a match or an applied compiler change.

The existing table dereference repair handles wider reads through a stable base but not the signed-versus-unsigned
byte pair. The target's `%lo(table)` load supplied the signedness; a local pointer alone did not name the symbol.
Separately, an earlier `lw`-to-`lbu` correction on a loaded pointer reduced the same diagnostic from 10,402 to
10,202; the existing `loaded_pointee_width` repair already generates that edit.

This is one observed improvement, not a general allocator rule or a verified match. Regression tests cover pointer
lifetime, control flow, preprocessor uncertainty and lvalue expressions; game source and data remain private.
