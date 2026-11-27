---
title: repeated blocks and sibling transplants
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O1
evidence: observation
date: 2026-11-27
---

What the function (or case) looked like and the exact compiler and flags:

N64 effect and menu functions of one game, matched by writing a matched sibling's C with this function's symbols and
constants, judged under the sibling's per-function profile (`-O1`), not the function's current `-O2` one.

The source change, and the measured result before and after (scores, the judge used):

- Three copies of "get a slot, spawn, set fields" written flat kept `0xFFFF`/`0x5B`/`0xFF` in saved registers
  (3636). Each copy in its own `do { } while (0);` stops cse at the loop note and was exact (0). Library:
  `repeated_run_scopes`.
- A `fail:` label shared by a call before a loop and one inside it belongs before the loop: written inside the loop
  body it gives the loop a second entry and loop.c hoists none of its float constants (6292 -> 405).
- `D[i] = table[k]; if (mode == 4) { D[i] += 0x2E; }` matched where a local holding the value did not (3175 -> 0): the
  stored value is reused from its register and the index stays in the register the target has.
- Two saved registers swapped between a constant and a float held as a word (35): the word in an `int`/`float` union,
  the loop's guarded body three one-shot scopes deep and the one statement that reads the constant `1.0f` six deep
  (0). Neither depth alone helps; the grid is region depth by statement depth.
- A look-alike that scores 300 with two `%hi`/`%lo` operand rows on the jump table had wrong case numbers: the base
  vote had moved the table (fixed in `rom/match.py`; the judge now lists the case destinations).
- Many "open" functions share one shape with a matched one (copies differing in tables and range bounds): after one
  matches, the others take the same source with the constants substituted, often at 0 on the first judge.

Failed experiments that would otherwise be repeated:

Automatic symbol-and-number substitution alone (no reading of the assembly) matched none of 50 look-alikes under 0.7
similarity: the added or removed branches need the target's assembly. A greedy chain of the library's families from the
flat three-copy source stopped at 3225.

What is controlled evidence and what is one function:

The jump-table vote has a regression test. The rest are single functions; the mechanisms (cse stopping at a loop note,
loop.c and a label inside the body) are hypotheses, not read in the compiler source.
