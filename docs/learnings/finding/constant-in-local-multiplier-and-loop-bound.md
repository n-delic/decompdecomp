---
title: constant in local multiplier and loop bound
kind: finding
compiler: SN GCC 2.8.1 (sn-n64-281)
evidence: verified
date: 2026-11-25
---

What the functions looked like and the exact compiler and flags: small N64 routines built by SN GCC 2.8.1 (`-G0 -mips3
-O2`): a float scale `g = x * 7000.0f` after a call, a counted loop `for (i = 0; i < 5; ...)`, and two widening
multiplies `(s32)((s64)coss(x) * 400) >> 15`.

The rule: a literal operand of a multiply or of a loop compare is folded into the insn (an immediate multiplier,
`slti`) or shares the register of an equal literal; written as a local set on the line before (`k = 7000.0f; g = x * k;`,
`n = 5;` above the loop) it is a pseudo of its own, live from its set to its use, so the load is a separate insn the
scheduler places, it holds a register across that span, and the loop compare is `slt` where the literal gives `slti`.
One `k = 400;` above two statements that both multiply by it is a different program from one local per statement.

Measured with the fast judge (read-only), on drafts reconstructed by inlining the matched local: float scale 35 -> 0,
a loop bound 1426 -> 0, two widening multipliers 2166 -> 0 and 3330 -> 0 (the block-wide local; the one-statement
child scored 1281 on the first). Offered by decomp-repair `constant_in_local` (children `constant_in_local_multiplier`
and `constant_in_local_bound`), gated on a register `slt` and a multiply in the target when its assembly is known.

Failed experiments that would otherwise be repeated: a bound declared with an initialiser at the declaration
(`s32 n = 5;`) did not reach 0 from the assignment form (465 left); a constant held in a local one block earlier and
passed to a call (`tgt = 180.0f;`) is not covered by this family.

What is controlled evidence and what is one function: four functions of one game under this profile, each a
reconstruction of a hand match, not generated from the compiler's source.
