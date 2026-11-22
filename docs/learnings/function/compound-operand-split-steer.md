---
title: A float chain's register rows closed by writing a negated product as an assignment and a compound assignment
kind: function
compiler: KMC GCC 2.7.2 -G0 -mips3 -mgp32 -mfp32 -O2
evidence: observation
date: 2026-11-22
---

What the function (or case) looked like and the exact compiler and flags:

twine `func_8006591C`, a 147-instruction N64 steering function that eases two floats toward a target (the target is
either read from a marker's two clamped ratios, or the fixed offset `-0.05f`, with a shared tail `y += (ty - y) * rate`).
KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2`, the project profile. The saved best (a Deep Permuter result at 70) had
fourteen register rows and nothing else: the second clamped value `ty` sat in `$f10` where the target has `$f0`, and
the clamp's constants took `$f0` where the target has `$f2`; the `-0.05f` the other arm loads into the same pseudo
went to `$f10` as well. The first arm already wrote `dx *= rate;`, the last arm `dx = (-x) * rate;`.

The source change, and the measured result before and after (scores, the judge used):

| edit | score |
|---|---|
| saved best | 70 |
| `dx = (-x) * rate;` -> `dx = -x; dx *= rate;` | **0** |

One edit, judged by the app's judge-worker (`fast_judge.py`) and then `compile_and_diff` (exact). No scaffolding added.
Why (hypothesis, not confirmed in the compiler's source): the one-line form expands to a temporary for the negation
that local-alloc gives `$f0`, so the shared pseudo `ty` (live across the constants of the clamp) cannot have it; writing
the negation into `dx` directly removes that temporary. decomp-repair `compound_operand_split` proposes it: from the
saved best, `prove_chain.py --families compound_operand_split` reaches 0 in one step (6 children).

Failed experiments that would otherwise be repeated:

The 617 children of the older families on the saved best (temporary hoists, allocation levers, declaration scopes,
global address tables) all scored 75 or worse; none touched this statement.

What is controlled evidence and what is one function:

One function. The family's other splits (an assignment from `a + b`, `a - b`, `a / b`) are proposed because the same
shape is plausible, not because they were measured.
