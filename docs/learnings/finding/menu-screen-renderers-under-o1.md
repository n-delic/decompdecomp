---
title: Menu and screen renderers that are mostly draw-helper calls are exact under -O1
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags: open N64 screen, menu and sprite renderers of
170 to 690 instructions that call draw helpers (many stack arguments) or write command words, with saved bests from
3,000 to 70,000 under `-O2`. All were exact under `-O1`, many on the first compile of a hand rewrite. The functions the
display-list finding (`display-list-builders-under-o1`) covers share the profile.

The source change, and the measured result before and after (scores, the judge used), all with the app's judge:

- Menu fields the target reaches from one saved address (`lh -6($s0)`) are members of one struct, not separate
  globals (3570 -> 0).
- A colour set and then passed as three arguments lived in a loop counter that was free there: `j = 0xFF; ...
  f((u16)j, (u16)j, (u16)j)` (290 -> 0, 90 -> 0; library family `narrow_value_in_counter`).
- A clamp before a call, `a = x; if (a >= K) a = K2; f(a);`, written as the call in each arm (320 -> 0; library family
  `clamp_call_arms`).
- A `case N:` arm that compares another value with N loads the constant again: an empty `do { } while (0);` after the
  label (383 -> 0; library family `case_head_empty_loop`). Scaffolding.
- Tests the target materialises as a flag (`xori; sltiu; bnel`): the same test written twice,
  `if (n == 10 || n == 10)` (300 -> 0). Scaffolding.
- A loop pointer and the saved start of the same table: `v = (vtx = table[a][b]);` instead of two statements (110 -> 0).
- A sum the target adds in another order than fold gives: an embedded assignment, `x + (t2 = t - 4)` (310 -> 0), and
  `(i * 2 + i) * 8` where the target shares `i << 1` with a table index (110 -> 0).
- A `switch` whose case values are not what the tree seems to say: read the constant the tree loads in the delay slot
  (`li 0xFF`) before spelling the cases.

Failed experiments that would otherwise be repeated: ternaries and `?:` for the clamp (445 at best); a separate `u16`
local for the colour (90 and 290 at best).

What is controlled evidence and what is one function: each item is the measured result on one function. The profile was
shown by the exact judge for every function it matched.
