---
title: phase donor second half
kind: function
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

The source change, and the measured result before and after (scores, the judge used):

Failed experiments that would otherwise be repeated:

What is controlled evidence and what is one function:

Symptom: register rows in a second phase (`mflo $a0; sra $a0,$a0,7` against `mflo $v0; sra $a0,$v0,7`, the clamp and the record
pointer in swapped registers), the instructions all right. A 181-instruction N64 function (KMC GCC 2.7.2) whose two halves each clamp a
scaled difference (`(x - field) * scale >> 7`, clamp to +-0x40) and add it to a halfword of a record.

Edit list from the saved best (40; its second clamp variable was the `.value` member of a register union a phase repair had added):
1. the second clamp variable as a plain local: 60 (worse alone: the union's pseudo had outranked the pointer);
2. the second record pointer (`void *T = (void *) (E)`) written through the first half's address variable (`D = (s32) (E);`, read as
   `(char *) D`): 15, then 10 with the sum written `(s32) ((arg2 * 2) + base)` (the operand order of the `addu`);
3. the product staged through the first clamp's dead variable, `D = a * b; V = D >> 7;`: 0. The same staging through a fresh local:
   40; through other dead locals: 10 to 665. Sharing the first clamp's variable for the whole second clamp: 145.

Why (hypothesis, read from `passes.py` and global.c, not proven): a pseudo allocated before a lower-priority conflicting one avoids the hard
registers that one prefers (`regs_someone_prefers`), so a combined product/shift pseudo of 9 refs (priority 1.8) outranked the record pointer
and took `$a3`; the first clamp's pseudo was already in `$a0`, and the product written into it shares that register at no priority cost.

Library: `phase_donor` (decomp-repair, `phase_donor_repairs`) offers the three edits as one child, then each subset; from the saved best the
child scores 0 under the app's judge. Controlled: that one function; the synthetic test covers the rewrite, not the compiler claim.
