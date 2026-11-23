---
title: giv locals set at loop top vs in the arms
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-23
---

What the function looked like: a 265-instruction save-slot scan (MIPS, N64). Loop one walks 16 slots with an outer
counter `i`: a command is submitted, then an `if (wait() == 5)` arm clears two strings (a 16-byte name and a 4-byte code,
each a `do { p[j] = ' '; } while (--j >= 0)` loop and a terminator `p[16] = 0`) and a `else` arm reads the slot and decodes
both strings. Every pointer the body uses (the flag, the name, the code, the raw record, its code byte, its byte offset)
steps with the counter by a different stride: 6 registers each initialised in the preheader and incremented at the end.
Loop two re-walks the slots with a second counter and a `stringCompare` call. The saved best was m2c's spelling, one
explicit cursor per stride, with search scaffolding (`do { ++i; } while (0)`, `do { goto L; } while (0)`, a second counter
`scan` beside the inner `j`): 1545 in the isolated judge (1125 as the app had recorded it under an older scale).

The source change and the measured results (`fast_judge`, which is the app's judge):

| edit | score |
|---|---|
| saved best | 1545 |
| inner counter `j` also used for loop two's `scan` (one counter for three loops) | 1560 alone; `j` then lands in `$s0` as the target has it (it crosses the `stringCompare` call, so global.c cannot give it `$v1`), which is why the rest needs it |
| every cursor written as the typed record table indexed by the counter (`gHeaders[i].used`, `gRaw[i].name`, ...), the `goto` tails as `return f(gResult)` (cross-jumping merges them), scaffolding dropped, loop two a `for` over `j` | 1775 (pointers as expressions, no locals) |
| the name and code pointers as locals set at the top of the body (`name = gHeaders[i].name;`) | 195: the instructions are the target's, only the preheader order, the increment order and three registers differ |
| the locals written into each arm as the expression, with a one-use local set right before each `p[16] = 0` / `p[4] = 0` store | 0 |

What decides it (compiler reading from loop.c, the measurements agree; the judge decides each child):
- `strength_reduce` records a giv for each insn that computes a value from the counter, in insn order, and *prepends* it to
  the biv's list (`record_giv`: `v->next_iv = bl->giv`). The preheader initial values (`emit_iv_add_mult(..., loop_start)`)
  and the increments are emitted in list order, so they come out in the reverse of the order the givs were met, and a group
  of identical givs (`combine_givs`: `g2->same = g1`, the first in the list, the last met) sits where its last member was met.
  A pointer set at the top of the body is met first, so it is initialised and stepped last; written in each arm, its group
  sits where the last arm uses it. `cc1 -dL` prints `giv at N reduced to ...` in list order.
- A store at a constant offset through a pointer local (`name[16] = 0`) is addressed `0x10($s1)` only through a *replaceable*
  giv local: `express_from` (the other way to derive an address from a giv) needs `g1->add_val` to be a CONST_INT, and a
  symbol-based pointer's additive part is a register or a symbol. Written as `gHeaders[i].name[16] = 0` the store is derived from
  the counter's own multiple (`lui $at; addu $at,$at,$s3; sb ...`: a register and three instructions the target does not have).
  A giv local is replaceable when it is set every iteration, or when all its uses are in the set's basic block
  (`last_use_this_basic_block`), so a local set just before the store, with no label between, is one.
- A top-of-body local cannot be made late by a second local in an arm: cse copy-propagates `name2 = name`, and the arm's
  own expression is cse'd to the dominating one. A local set in one arm and used after an inner loop's label is not
  replaceable (a `move` of the reduced register stays in the arm).

Failed experiments that would otherwise be repeated (all in the isolated judge, 195 stands as the base):
- every statement order of the then-arm (24) and the else-arm (6), the order of the two top locals, per-arm locals
  (2010), locals after the wait call (1132), `name2`/`code2` locals in the else arm (195: cse merges them), casts and
  re-associated spellings of the arm's expression (195), top-level locals for the flag, raw pointer, byte offset, name,
  code and code byte in every order (2641 combinations, best 195), explicit pointer cursors for name and code beside the
  counter's givs (415), the same with the code byte (2695).
- The typed record matters: the same accesses as `(char *) sym + K + i * 24` with separate symbols per field stood at 7780,
  with one symbol per table at 7555, with an opaque `struct { char b[24]; }` per symbol at 6930. Strength reduction of
  the second loop's reads changes with the spelling (a pointer instead of `lui`/`addu`), so the record's real fields are
  part of the match.
- The library's `counter_companion`, `stepped_induction_index` and `record_slot_table` do not fire on this saved best (the
  `do { ++i; } while (0)` step, a companion initialised from another companion, field offsets other than 0) and the
  char-pointer spelling they write scores worse than the cursors; the typed record needs the symbols' addresses, which a
  repair's context does not carry (renamed symbols do not show them).

Controlled evidence and one function: the family `loop_giv_local_arm_inline` (decomp-repair, `tests/test_loop_giv_local_arm_inline.py`)
takes the 195 source to the 0 one as its first child. The preheader-order rule is read from loop.c and confirmed by the
dumps of this function and by moving one group's last member (a read through `name[0]` in the arm moved the code group
ahead); it is one function's measurement, not a table over several.
