---
title: A compound update of memory launches its single-set add in sched1; a two-set local does not
kind: finding
compiler: SN64 GCC 2.8.1 -G0 -mips3 -O2 -mfp64
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:
A 182-instruction N64 allocator (`sn-n64-281`, `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`). One block
reads a count from a table, adds `count * 12` to a header field, scales the count by 8 for a call argument and adds the
scaled value to a running end before the call:
`count = T[i]; hdr.used += count * 12; count *= 8; end += count; f(buf, *a, *b, count);`
The target loads the count into `$a3`, runs the `*12` chain and the field's store, then `sll $a3, $a3, 3`, and has
`addu $s1, $s1, $a3` (the end) in the `jal` delay slot.

The source change, and the measured result before and after (scores, the judge used):
The saved best (270) kept `end += count` after the call inside a `do { } while (0)` fence (search scaffolding); without
the fence 945, the shift placed ahead of the `*12` chain. Writing the field update through a local with two sets,
`t = hdr.used; t += count * 12; hdr.used = t;` (a fresh local, or an unused one): 945 -> 145, with or without
`end += count` moved above the call. Isolated judge (the app's judge-worker), confirmed with `compile_and_diff` (145).

The rule, read in sched.c (2.8.1 source in the toolchains folder) and the `-dS` dumps of both spellings: sched1 schedules
backwards; when an insn is scheduled, each predecessor that becomes ready and sets a pseudo set only once that is live
(`birthing_insn_p`) gets the launch priority (`adjust_priority`, `7f000001` in the dump) and is picked next. The
compound update's add sets a one-set pseudo, so after its store the whole `*12` chain is launched in a row, and the
end's add (a multi-set user variable, ordinary priority) and with it the shift wait until the chain is done: the shift
lands before the chain. Through a two-set local the add is not launched, the end's add (later INSN_LUID) wins the tie,
and the shift is launched right after it, between the field's add and its store.
Same mechanism as `rmw_user_local_steps` (a masked update) and decomp-repair `compound_update_local` (0.6.70) offers the
`+=`/`-=`/`|=` etc. form for a global, a member, an element or a typed dereference.

Failed experiments that would otherwise be repeated:
`count <<= 3` (900), `count = count * 8`, `end += (count *= 8)`, `count * 8` written twice, the end's add after the call
(945 each); the end's add into a new single-set local `total = end + count` (1050-1055: it is launched instead and
the a3 copy loses to the loads by potential hazard); a new local for the field's update assigned once
(`t = hdr.used + count * 12; hdr.used = t;`, 945: one set, still launched).

What is controlled evidence and what is one function:
One function. The sched.c rule is read from the compiler source and both dumps; the ordering of ready insns by
priority, then class, then INSN_LUID is `rank_for_schedule`.
