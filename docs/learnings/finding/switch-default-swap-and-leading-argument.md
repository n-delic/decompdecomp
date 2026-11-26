---
title: switch default swap and leading argument
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

Two N64 dispatchers (a `switch` over a `u16` field, jump table, `-G0 -mips3 -mgp32 -mfp32 -O2`), both matched by hand.
- A (13 instructions): the draft's `case 7: call(); default: return;` had a table over 1..7 where the target's check was
  `sltiu 0xc` (1..12): 150, closed by `switch_range` already. Once the range was right, `case 12:` sat on the call arm
  where the game's table sent 7 to it: score 1, every instruction equal, the judge's linked jump-table rows `7 | EB00 |
  EB08` and `12 | EB08 | EB00`.
- B (8 cases): `func_8007081C(var_a1)` in two arms where the target loads the constant into `$a1` and leaves `$a0` (the
  caller's pointer) alone: 10, two register rows. With the call `func_8007081C(arg0, var_a1)` and the prototype
  `(void *, s32)`: 1, with rows 3, 4, 5, 6, 9, 12 all `EE20 | EE08` (the target sends them past the default arm).

The source change, and the measured result before and after (scores, the judge used):

The app's standalone judge (`fast_judge.py`) on the project's own target.
- A: `case 12: call();` -> `case 7: call(); case 12: default: return;` (the written label renamed to the missing case, the
  old one written in front of `default:`): 1 -> 0. New child `linked_switch_default_swap` (targeted_switch_labels).
- B: `leading_argument_widen` (new module): the callee has one prototype in the source and two calls, the target's `jal`s
  receive `$a0` untouched (nothing in the function writes it; the jump table's `jr $v0` stops the path walk, so the
  family falls back to "no instruction writes the register"): 10 -> 1. Then the existing `linked_switch_empty_arm`
  (`case 3: case 4: case 5: case 6: case 9: case 12: break;` before `default:`): 1 -> 0.
- The existing families already reached the rest: `switch_range` (150 -> 0 on A), `linked_switch_empty_arm` (1 -> 0
  on B once the argument was there), driven by `prove_chain.py`.

Failed experiments that would otherwise be repeated:

- `passthrough_argument` could not do B: it widens a prototype only when the call is the callee's only one, and
  `argument_passthrough_to_callees` only handles callees without a prototype. `incoming_calls` returned nothing for a
  function with a jump table.
- `prove_chain.py --families all --children 100` on B's argument-fixed source printed "no child improves": 29 of the 129
  children were cut off and the diff-gated children (`linked_switch_empty_arm`) come after the draft children. Name the
  families (`--families linked_switch_empty_arm,linked_switch_labels`) when the table rows are the lead.
- The target merged both calls of the callee into one `jal` (cross-jumping), so a "calls as often as the target"
  condition fails; the family accepts fewer `jal`s than calls.

What is controlled evidence and what is one function:

One function each (A and B), unit tests with synthetic code in decomp-repair (`tests/test_switch_default_swap.py`,
`tests/test_leading_argument_widen.py`). The "nothing writes `$a0`" evidence is weaker than the path walk (an earlier
call on a path may have clobbered it); the judge is the proof.
