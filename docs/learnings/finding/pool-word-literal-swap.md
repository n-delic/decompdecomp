---
title: pool word literal swap
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-25
---

What the function looked like and the exact compiler and flags: an object update built by SN GCC 2.8.1 (`-G0 -mips3
-O2`) that adds a float pool word to a field (`o->f += D_word;`), with the word a named `extern f32`.

The rule: the opposite direction of float-literal-through-global. The target loads the word through `$at`/`$gp` as a
named data symbol; the draft's literal (`o->f += 2.5f`) compiles to the compiler's own pool entry with a different
register order and, under `-G8`, a `.rdata` literal. Naming the word at some uses and not others (all uses, `extern
const f32`, the first use only) are separate children because identical words at every use changed allocation in one
routine.

Measured with the fast judge (read-only) on a draft reconstructed by replacing the named word with its literal:
565 -> 0 on one function; three other reconstructions were already 0 with the literal under this profile, so the
direction only matters where the target loads the word as a symbol. Offered by decomp-repair `pool_word_literal_swap`,
from the caller's `words` and the target's `lwc1 %lo(sym)($at)` or `%gp_rel(sym)($gp)`.

Failed experiments that would otherwise be repeated: strings (`"name"` versus a data symbol) are not covered.

What is controlled evidence and what is one function: one function; fires on any function whose draft has a literal the
target loads as a symbol (272 of 2,068 saved sources of one game), so it is a broad proposal.
