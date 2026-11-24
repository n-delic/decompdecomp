---
title: a callback stored into a record and called back through it is one function-pointer local assigned twice
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281), -G0 -mips3 -O2 -mfp64
evidence: verified
date: 2026-11-24
---

What the function looked like: a 90-instruction N64 object update. Inside one branch it stores a callback's address into
a field of its own record (`F(arg0, 0x9C) = &func_A`), fills a second record and stores another callback into that
one's field at the same offset, stores a third callback into a global record, calls a helper, and finally calls through
the second record's field with the record as the argument (`F(second, 0x9C)(second)`). The target builds `func_A`'s address
late, in the register the call that follows needs, and loads the called field into the register it jumps through.

The source change, and the measured result before and after (scores, the project judge): the developer kept one
function-pointer local, assigned twice

    fp = func_A;  F(arg0, 0x9C) = fp;  ...  fp = F(second, 0x9C);  fp(second);

On the m2c draft the chain scored 880, then `field_struct_view` 115, `callback_pointer_store` 35, `allocation_lever` 10;
the 10 left was the callee's missing first argument (written by hand, 0), which no callback family can supply. On the
draft alone the three children scored: read-back only 880 (no change), the first store through the local 800, the second
store (the one the call reads) 1220, the third 830. Reusing the local for every callback store of the function was worse
than the first store only, which is why the family offers one store per child and not all of them.
Library: decomp-repair `callback_pointer_local`, children `callback_pointer_local` (the read-back) and
`callback_pointer_store` (one earlier store at the call's offset, the first first), gated on the target's `lw` of the
call's offset followed by `jalr`/`jr` through that register and a `lui %hi(func)` for the stored function.

Failed experiments that would otherwise be repeated: the local for the read-back only (no change on this function); the
local for the store that feeds the same call (1220: the store and the call then share the field the original loads
separately); every store through the local.

What is controlled evidence and what is one function: one function, verified (0) through a chain with one by-hand
argument. Whether the address is built late is not visible from the target alone, so the gate is the load-then-jump and
the built address, not the lateness; over a project's 2,206 functions (nothing judged) the load-then-jump is in 1,342
targets and the family with a stored, built address fires on 5 drafts (four of open functions) and 4 saved bests; on
the other three open functions it fires on, the children scored the same as the parent or worse (no matched function's
saved best fires). Not covered: a callback stored through a local pointer's field at a computed offset, and calls
through a table of callbacks.
