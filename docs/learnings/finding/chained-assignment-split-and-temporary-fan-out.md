---
title: chained assignment split and temporary fan out
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-25
---

What the function looked like and the exact compiler and flags: an object update routine built by SN GCC 2.8.1 (`-G0
-mips3 -O2`) that copies one field to two others and passes the value on:
`o->3C = o->34; o->38 = o->34; d = f(o, 0, o->38);`.

The rule: m2c names the value (`t = o->34; o->3C = t; o->38 = t; d = f(o, 0, t);`); the game loaded the field twice and
read the call's argument back from the LAST field stored, which changes which register carries the value and the
order of the loads. `a = b = t;` is a third spelling (one evaluation, the stores read one register).

Measured with the fast judge (read-only) on a reconstructed draft: 1245 -> 0 with the argument read from the last
field; the other two spellings scored 210 and 0 (the argument from the first field was also 0). Offered by decomp-repair
`chained_assignment_split` (the fan-out, the chain split into a read-back or a second store, and two adjacent
statements joined into a chain).

Failed experiments that would otherwise be repeated: none recorded.

What is controlled evidence and what is one function: one function; the chain forms are untested here.
