---
title: counter read fence before may alias store
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-23
---

What the function (or case) looked like and the exact compiler and flags: the last five register rows of a
118-instruction menu update (KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`; the earlier steps are in
call-result-join-copy-and-tick-order.md). Target: `and $v1,$v1,$a0; sll $v0,$v0,3; or $v1,$v1,$v0; lhu $v0,counter;
sw $v1,0x10($a1); lbu $v1,flag; addiu $v0,$v0,1; sh $v0,counter`: the counter is loaded after the `or`, in the register
the shift's result has just freed.

The source change, and the measured result before and after (scores, the judge used): from the 60 form
(`*(volatile u32 *)&p->color = packed; counter++;`), read the counter into a `u16` local of its own before the store,
the read alone in a one-shot scope, and write the sum after it:
`do { c = counter; } while (0); *(volatile u32 *)&p->color = packed; counter = c + 1;` -> 0 (isolated judge, then the
app's compile_and_diff). Wrapping the whole mask/shift/or run instead (`do { m = ..; t = ..; packed = ..; } while (0);
c = counter;`) is also 0. The rule: sched.c makes the insn after a loop note depend on every insn before it, so the
load cannot rise above the `or`; `c` has one set and one death in one block, so local-alloc gives it the `$v0` the
shift freed and the mask stays tied to the loaded colour in `$v1`. Library: decomp-repair `rmw_read_fence`.

Failed experiments that would otherwise be repeated: the same pre-store read with no scope, 425 (sched1 hoists the
load above the shift: priority 1, and at the slot before the store the memory unit blocks it, `-fsched-verbose=4` shows
"blocking insn ... for 2 cycles", so the `or` is taken there); the counter in the masked value's local (the 25 of
`volatile_rmw_counter_share`: two deaths, a global pseudo, `$a0`; it can never match, since the flag already holds `$v1`
while the counter is live); volatile casts on both halfword reads (845), a volatile struct store or a plain store (900:
the reads move above it and the store fills the branch slot); the scope around the read, store and increment together
(475) or opening before the field store (80).

What is controlled evidence and what is one function: the loop-note dependence is read from sched.c; the outcome is
one function.
