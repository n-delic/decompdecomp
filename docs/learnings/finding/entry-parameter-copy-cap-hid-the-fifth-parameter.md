---
title: entry parameter copy cap hid the fifth parameter
kind: finding
compiler: KMC GCC 2.7.2 (sn-n64-281), -G0 -mips2 -O1
evidence: observation
date: 2026-11-24
---

What the function looked like and the exact compiler and flags: an 87-instruction N64 vector function (KMC GCC 2.7.2, -G0 -mips2 -O1) taking five pointers. It copies three floats from the first into the fourth, then writes the same three through the fifth (o32's first stack-passed argument, `lw $v0, 0x10($sp)`), then runs min/max loops that read the fifth again. The compiler hoisted that stack load to the top; the target loads it after the three stores through the fourth (60, two differing rows).

The source change, and the measured result before and after (scores, the judge used): copy the parameter into a local after the three stores and overwrite the parameter (`p4 = arg4; arg4 = 0;`, later uses on `p4`; as `entry_parameter_clobber` writes it, the parameter renamed `arg4_in`), 60 -> 0 (fast_judge). The plain copy without the overwrite also reached 0 in this spelling. `entry_parameter_copy`/`entry_parameter_clobber` already produced that child, but capped each family at 8 children in parameter order, and the first two parameters filled it, so the fifth never reached the judge (`prove_chain --families all` stopped at 60 over 457 children). decomp-repair 0.5.1017 interleaves the children across the parameters (cap 24 per family) and `prove_chain` reaches 0 in one step.

Failed experiments that would otherwise be repeated: pointer local set at other points among the copies, two live sets, copy-back, const qualifiers, f32/integer locals, temps for the loads of the first pointer, every order of the six copy statements, struct copies (2400), a volatile or address-taken parameter (700 to 1000), flags. Only the copy placed right after the earlier stores, with the parameter overwritten or renamed, changed where the stack load is scheduled (hypothesis, not confirmed in the compiler source).

What is controlled evidence and what is one function: one function. Over 1019 matched functions of the same project the family offers 1780 children on 535 of them (at most 40 on one), judged by none.
