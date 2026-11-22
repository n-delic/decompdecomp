---
title: a pointer reloaded at each level of a nested walk: one local per level, and what hid the library's split
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

A 249-instruction N64 function (an object-list search: a walk over a record's linked children, three levels deep, each
level loading a child pointer, comparing and stamping a halfword in the child's record, then calling the same
callee) sat at 110 in the app's judge. The rows were registers only: the first level's record pointer in `$a1`
and the stamp in `$v1` where the target has them the other way round at the innermost level, and the first call's
argument `move $a1,$s1` hoisted above the two loads where ours scheduled it after them.

Edits from the saved best, each judged by the app's judge (`fast_judge`, then `setup.py try`):

| edit | score |
| --- | --- |
| saved best | 110 |
| innermost level's pointer (`v1 = a0->0x20`) in a local of its own | 80 |
| the first level's pointer local (`s0`, reused at the second level by `s0 = v1->0x58`) renamed in its first phase only | 30 alone, 0 after the first edit |
| both | 0 |

Why the schedule moved too: with the first level's pointer shared with a later reload (a long-lived pseudo with six
more references) the first scheduler had the load of that pointer ahead of the argument copy; with its own pseudo the
order the target has fell out. This is read from the dumps (`sched` order differs between the two sources, same
instructions) and measured; I did not trace the priority arithmetic in sched.c, so the reason is a hypothesis.

What the permuter did not do, and why (this is the finding): the library already had the split
(`phase_lifetime_repairs.splits`, `split_integer_phase`, which proved 110 -> 30 -> 0 from the saved best once its
text was cleaned), but it never fired on the saved best, for three reasons the agents' own edits had introduced or the
library had:

1. `void *s5, *s0, *s2;` (a list of declarators). The pointer-local pattern reads one declarator per statement.
2. `#define NF ((found ^ 1) & 1)` (an object macro reading a local). `allocator_call_guards.context` declines the whole
   function when a macro can read a local out of its sight, which switches off every family built on it.
3. A bug: `splits()` matched the pointer/float locals by declaration object identity, but `local_flow.analyze`
   caches one flow per text and hands back the first call's declarations, so the second call on one text dropped every
   float and pointer local. A beam that revisits a text, or a family that asks twice, saw nothing.

decomp-repair `normalized_phase_split` writes the declarators one per line and expands the one-line object macros that
read a local (the same program), runs the library's own splits, and offers the splits applied one after another as one
child (110 -> 0 from the saved best in one step). It is wired into the drafts and the Deep Permuter's local lane. The
saved best's `NF` and declarator list are the agents' spelling, not the game's; the matched source has neither problem.

Controlled: the 110 -> 30 -> 0 chain and the cached-flow bug (a unit test fails without the fix). One function:
the 110 start and the scheduling explanation.
