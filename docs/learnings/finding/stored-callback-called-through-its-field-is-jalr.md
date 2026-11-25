---
title: a callback stored in a field and called right after is called through the field
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp32
evidence: verified
date: 2026-11-25
---

What the function looked like: spawn and state-change helpers: `lui/addiu` builds a callback address, `sw` stores it at `0x9C`
of a just-allocated record, then `jalr` calls it with the record. m2c writes the call by name (`M2C_FIELD(p, ... (**)(void *),
0x9C) = cb; cb(p);`), which compiles to `jal cb`.

The source change, and the measured result before and after (scores, the project judge): call through the stored field,
`o->x9C = cb; o->x9C(o);` (in the draft: `M2C_FIELD(p, T (**)(void *), 0x9C)(p);`), which the compiler cannot fold to the
callee. Three matched functions: 4430 -> 4030, 2993 -> 2593, 2801 -> 2601 by the pair alone (the rest of each still differs);
the library family `callback_call_through_field` (decomp-repair 0.5.1045) gates on a `jalr` and no `jal` of the name.

Failed experiments that would otherwise be repeated: a function-pointer local (`callback_pointer_local` is the other shape: a
callback loaded back from a field and called).

What is controlled evidence and what is one function: verified on the final matched sources of three functions; none open fires.
