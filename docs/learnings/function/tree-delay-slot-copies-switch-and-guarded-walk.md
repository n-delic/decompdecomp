---
title: m2c's per-path delay slot copies hide a switch; the walk is a while with a break
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-28
---

A 293-instruction N64 linked-object update (a head dispatch, a state tree, a parent walk, a copy down the children).
Saved machine draft 7285 after 93 attempts; matched. All scores are the app's judge.

The source change, and the measured result before and after (scores, the judge used):

- m2c wrote `cursor = obj;` (the delay slot of each jump to the join) on every path of the state tree, some by
  `goto` into a labelled copy. Written once after the tree, the tree is the `switch` it was: 7285 -> 1630 with the walk
  below (`path_copy_hoist_switch`).
- The parent walk, a guarded `goto` loop whose found arm leaves, is `while (p->parent != 0) { ..; if (..) { ..; break; } }`
  (`goto_walk_break`). The `for (;;)` with `continue` from `goto_backward_loop` stayed 720 away at the end.
- A halfword table declared `char []` and read through byte arithmetic: indexed `u32 []` (`indexed_symbol_records`,
  1630 -> 710) and a `u16 []` store (`scaled_byte_table_element`, 710 -> 310).
- `sel = obj->type; switch (sel)` as `switch (obj->type)`, the arm's `== sel` as its case value
  (`switch_selector_inline`, 310 -> 200): only then does the target's second load of the field appear.
- `0xFFFF` stored through `s16 *` as `u16 *` (`field_width`, 200 -> 0).

Failed experiments that would otherwise be repeated: `prove_chain.py` over all families with a 50-variant cap got only
6880 (2563 children at the first step).

What is controlled evidence and what is one function: the chain is measured on this one function.
`embedded_if_tree_switch` never reached this tree before: it stopped after a function's first two trees, and declined
a tree whose arm read the tested variable.
