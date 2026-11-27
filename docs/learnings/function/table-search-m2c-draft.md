---
title: A frame table searched for an id, from its raw m2c draft to exact
kind: function
compiler: sn-n64-281 -G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64
evidence: verified
date: 2026-11-27
---

A 230-instruction routine copies a 61-word table and a 51-byte table to its frame, finds an id in the first with a
`u16` index (returning at an end marker) and makes one call per arm of a long `if` chain, which cross-jumping
merges into one `jal`. The saved best was the raw m2c draft (17193 in the app's judge).

The first hand-written source was exact: two record initialisers, `while (ids.ids[i] != id)`, and
`load(names[i], count)` in every arm with `extern char *names[];`.

Chain from the saved draft, each step a decomp-repair family (exact judge, `prove_chain.py`):

| step | score |
|---|---|
| `slot_scalar_records` (copy loops become assignments, the `spXX` words become the records) | 6860 |
| `call_into_arms` | 1980 |
| `guarded_search_else_loop` (with the stray call arguments dropped) | 1200 |
| `shared_value_inline` | 480 |
| `linear_subscript_view` (`extern s32 table[];`, `table[i]`) | 0 |

Failed or misleading on the way:

- The search loop rewrite alone scored worse (1280 -> 4590) while the merged call still passed the index and the
  table's address as arguments; with them dropped it was 410. The two are offered as one child.
- With every temporary written out, `*(s32 *)((i * 4) + table)` on `extern char table[]` stayed at 480; only the
  subscript of a word (or pointer) array was exact.

What is controlled evidence and what is one function: all from one function; the library tests cover the rewrites
on a reduced source.
