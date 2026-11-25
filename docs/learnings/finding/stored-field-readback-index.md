---
title: stored field readback index
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: observation
date: 2026-11-25
---

What the function (or case) looked like and the exact compiler and flags:

A small N64 object initialiser (SN GCC 2.8.1, `-G8 -mips3 -O2`) stored a constant into a record's field and then read a table at that same number through the field: `P->x70 = 1;` then `D[1]` for the first read and `D[P->x70]` for the next two. m2c writes every read with the constant, which keeps a register for the folded address through the function; the target reloads the field (`lw` of the field, `sll`, `addu`, `lw`). The stores to the record's other fields in between are not known to leave the field alone, so the read is a real load.

`stored_field_readback_index` rewrites a subscript equal to a just-stored integer constant (or a local holding the stored value) as the field. The matched function with the readback reverted to the constant: 945 -> 0 in one step. On two open functions it fires on (saved bests) it did not help (3749 -> 4179, 3546 -> 3546).

The source change, and the measured result before and after (scores, the judge used):

See above (the app's own judge on the project's profile, read-only).

Failed experiments that would otherwise be repeated:

Every read written through the field (the first read left as the constant) is the shape that matched; the child for all reads together scored worse alone than the two later reads. Nothing but plain statements and stores to other fields of the same base may sit between the store and the read: another base may alias and a call may change the field.

What is controlled evidence and what is one function:

One matched function, so observation, not a rule. The temp form (`t = e; P->f = t; ... D[t]`) is covered by the same module but no function in the project showed it.
