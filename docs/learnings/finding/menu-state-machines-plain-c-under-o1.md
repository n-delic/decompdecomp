---
title: menu state machines plain c under o1
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

Open N64 menu handlers and scene sequencers of 169 to 824 instructions: a `switch` on a state halfword of a record array,
calls, byte counters, a transition wait. Their saved bests (2,000 to 41,000) were drafts under `-O2`. Written as plain C
from the assembly under `-O1`, many were exact on the first compile and the rest within a few named edits.

The source change, and the measured result before and after (scores, the judge used), all with the app's judge:

- A global read in a loop (or only compared) whose address the target forms once: one-member record,
  `extern struct { s16 value; } G;` (265 -> 0). An array indexed in a loop whose address the target does not keep:
  array of one-member records (465 -> 0). Library: `global_read_record`, `array_element_record`.
- A row product plus a column byte indexing a flat table: name the row product in a block local,
  `s32 row = G * 0x110; n = T[row + sel];` (405 -> 0).
- A colour select computed wholly in the argument register: the steps written into one `s32` local
  (`c = x ^ 0xFF; c = c != 0; c = -c; c = (u8) c; c |= 0x60;`), and a `u16` load re-narrowed in a call loop
  (`v = (u16) v;`) so loop.c leaves another table's address unhoisted (90 -> 0). Library: `masked_select_steps`,
  `loop_narrow_self_reassign`.
- A `switch` whose arms share a store: `default:` first or an arm falling into `default:` decides which arm gets its own
  jump (970 -> 250); a store both arms of an `if` end with, written in the arm and again after it, matched
  (`G = 0xF0; break;` then the other arm: 250 -> 0).
- A comparison's operand order decides which of two loads comes first (`a > b` against `b < a`).
- Two key tests that share their body, each written out in full, are merged by cross-jumping as the target has them;
  a `goto` to a shared copy kept the address in another register (2195 against 35).
- A case ending in a store both branches of an earlier test also make: write it unconditionally after the `if`.

Failed experiments that would otherwise be repeated:

One handler stays at 430: the target takes the address of a record member, derives the record pointer from it and stores
through that pointer; `menu = (Menu *) (pc - 3)` keeps `pc` as the base instead, and a second symbol for the member did
not help. A drawer of the same game stays at 250: a hoisted literal and the first parameter exchange two saved registers,
and `do { } while (0)` around calls that name the literal also raises the x offset above the loop's record index.

What is controlled evidence and what is one function:

Each item is the measured result on one function. The profile was shown by the exact judge for every matched function.
