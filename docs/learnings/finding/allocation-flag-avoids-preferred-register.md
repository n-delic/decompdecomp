---
title: allocation flag avoids preferred register
kind: finding
compiler: KMC GCC 2.7.2 -O2 (global.c)
evidence: observation
date: 2026-11-23
---

Rule (global.c `find_reg`, read in the source folder; the measurement below is one function): a global pseudo is allocated
in order of `floor_log2(refs) * refs / live_length`, and in the first of its two passes it skips every hard register in
`regs_someone_prefers`, the union of the full preferences of the LOWER-priority pseudos that conflict with it
(`prune_preferences`), and every register nothing has used yet. So a high-priority pseudo that overlaps a lower-priority
pseudo with a copy preference for `$a1` (a pointer copied into the first call argument: `move $a1,start`) takes `$a2`,
and the lower one then gets `$a1`, although its own priority is far below the first one's.

Evidence: a 0/1 flag (priority 2.8, 12 references) and a character copy (priority 1.2, 9 references) in a loop. The target
has the copy in `$a1` and the flag in `$a2`; our source gave the flag `$a1`. Naming the flag's variable also for a
loop-2 value (`temp = ...; if (temp | c)`), so its live range overlaps the start pointer that prefers `$a1`, gave exactly
the target's loop (35 -> 15), but the shared variable then took `$a2` in loop 2 as well (the target has `$v1` there). decomp-repair's `disjoint_local_merge` now offers that pair (its 12-pair cap had hidden it).
Raising the copy's priority above the flag's needs 16 weighted references (each `do { } while (0)` adds one: not
faithful). So the missing step is a source in which the flag conflicts with a pseudo preferring `$a1` without being one
variable with it. Found: the last loop's truncation and bound give it that conflict (finding/preferred-register-carried-from-a-later-loop, decomp-repair `loop_preheader_role_reuse`, 15 -> 0).

How to check the next time: `passes.py` prints each pseudo's priority, register, and the hard registers it conflicts
with; a pseudo that "prefers" a register in the listing is the one to look for; two pseudos in adjacent registers
that are swapped against the target and have priorities far apart are this case.
