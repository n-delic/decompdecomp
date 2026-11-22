---
title: display list arm locals
kind: function
compiler: gcc-2.7.2-kmc -O2 -G0 -mips3 -mgp32 -mfp32
evidence: verified
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags: a 597-instruction display-list state
function (a switch on a mode, then a second switch whose arms each append two command words: `packet = cursor;
cursor = packet + 2; packet[0] = a; packet[1] = b; return;`), KMC GCC 2.7.2 `-O2 -G0 -mips3 -mgp32 -mfp32`. The saved
best (365, registers only: 73 register rows, every instruction otherwise the target's) declared the pointer and the two
words once at the top and assigned them in 24 arms. The target has the pointer in `$v1` and the second word in `$a0`;
the saved best had them the other way round in every arm, and in the shared tail that cross-jumping builds from the arms.

The source change, and the measured result before and after (scores, the judge used): one local per arm for each of the
three scratch values (`packet_1 .. packet_24`, the words likewise; webs of one local that a later read could see around
were left together), 365 -> 0 in the standalone judge and in the app's `compile_and_diff`. One name for all arms is one
global pseudo with ~96 references (global.c, allocated first, so it takes the register the target gives the word);
per-arm locals are short pseudos born and dying in their block, allocated by local-alloc, and the pointer (more
references) gets `$v1`. Passes dump: `passes.py` showed pseudo 78 (96 refs, 172 live) first in the allocation order.
decomp-repair `local_web_split` does the split.

Failed experiments that would otherwise be repeated: splitting one arm alone is worse (365 -> 1055): that arm's tail
now differs from the others and stops being merged by cross-jumping, which also changes the registers of the others.
Splitting all but a few arms is as bad (1055 .. 8540 by arm). Only the whole set at once reaches the target, so a
greedy chain of one-arm children never finds it (`prove_chain` on the 365 best with `case_temporary_split`,
`branch_pointer_lifetimes`, `reused_temp_split`, `arm_private_rename` and eight more: no child improves).
Block-scoped locals and function-level locals gave the same result. A first attempt that missed an assignment
separated from its group by another statement (`var = K; D_flag = 6; temp = K2; packet = ...`) left a stray constant live
across 80 instructions in a callee-saved register (10308): every assignment of a web belongs in it.

What is controlled evidence and what is one function: one function (verified by the app's judge, byte-identical). The
rule (a shared scratch local assigned in many arms is one global pseudo; per-arm locals are local-alloc quantities, and
the arms' tails only merge when all agree) is the same one `heap-bump-arm-complement-tie` and the arm-pointer learnings
state for two arms; here it needed 24 at once. Hypothesis, not read in the compiler source: that the pointer's priority
over the word decides `$v1`.
