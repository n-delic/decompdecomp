---
title: a product left in $a2 and shifted into $a0 is not a third argument
kind: finding
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: observation
date: 2026-11-27
---

A 104-instruction N64 buffer allocator calls a two-argument allocator thirteen times. Four sizes are a screen
product doubled: `mult; mflo $a2; jal alloc; sll $a0, $a2, 1`. m2c read `$a2` as set before the call and passed the
product as a third argument (`alloc(area * 2, 6, area)`). The compiler puts the product in `$a2` without one.

From the raw m2c draft, scored by the app's judge: 3568; stray arguments dropped, 1609; products written inline
(`assignment_at_use` twice), 1324 then 0.

`target_call_arity` offered nothing for two reasons, both fixed in decomp-repair (pull request 2188): it read the
`sll` in the delay slot as a store because the name starts with `s`, and it did not count a register that arithmetic
reads into an earlier argument register as used up.

The function's saved best had moved away from the raw draft (an `s8` global that receives a pointer, two of the calls
reordered, base-pointer locals; 2543). Dropping the stray argument from its plain form (900) only reached 880: the
store width (700) and call order (220) edits were needed too, so the raw draft was the better start.

One function; the library's tests cover the two readings with made-up assembly.
