---
title: strip blitter: a short difference and a clamp's result share one scratch local
kind: function
compiler: SN GCC 2.8.1 (sn-n64-281) -G0 -mips3 -O2
evidence: verified
date: 2026-11-26
---

What the function (or case) looked like and the exact compiler and flags:

A 477-instruction N64 texture strip blitter (gDP LoadBlock/TextureRectangle commands on a display-list cursor, two
pixel-size cases), the project's default SN GCC 2.8.1 profile. The saved best was 370; `prove_chain --families all
--children 40` found nothing because 1393 of the 1433 children were cut off. With every child scored (`--children
1500`), `reassigned_local_inline` stored `D_80107890 = (y0 << 2);` instead of through the loop counter `n`: 370 -> 310.

Of the 310, 300 were two "relocation-rendering" rows (`lw/sw 0x7890($t5)` through a copy of a `lui`, which the
target's assembly prints without a symbol): they are byte-identical after linking, and the judge decides the match by
the linked bytes (`rom/match.diff`). The real remainder was one register: the entry's `y1 - y0` is `$v1` in the
target and `h`'s saved register `$s3` in ours (`h = y1 - y0; texh = h + 1;`, `h` is set again later and lives across
the calls).

The source change, and the measured result before and after (scores, the judge used):

Two edits together, fast_judge (the app's judge) and then `compile_and_diff`, which recorded the match:

```c
    top = y1 - y0;           /* was h = y1 - y0; top was declared and unused */
    texh = top + 1;
    ...
    G(0xF3000000, 0x07000000 | (((top = ((texw * n - 1) < 0x800 ? (texw * n - 1) : 0x7FF)) & 0xFFF) << 12) | ...);
```

- the difference in `top` alone: 905 (a block-local value: local-alloc gives it `$v0`, and sched2 can no longer lift
  the `slti` over the register saves);
- the LoadBlock MIN result assigned to `top` alone: 310 (no change);
- both: 0.

Reading from the dumps (`passes.py --sched-verbose`): sched1 orders the entry identically in every variant (subu y,
addiu texh, slti, bnez; birthing insns get `max_priority`, ties go by luid). The register is global.c's: one
multi-set pseudo whose clamp web is live while the `?:` compare's result is in `$v0` cannot take `$v0`, so it gets
`$v1`, the register the target uses at the entry and in the clamp. Hypothesis from the dumps, not from reading
global.c for this case.

decomp-repair `ternary_scratch_share` (0.6.51) offers exactly this pair as one child (and each half alone): a pair
`X = E; Y = ... X ...;` whose `X` is a long-lived multi-set local, and a parenthesised `c ? a : b` operand, both
written through an unused local (or a new `scratch`).

Failed experiments that would otherwise be repeated:

- `texh = y1 - y0 + 1;`, a single-set extent local, `h = y1 - y0 + 1; texh = h;`: 905 / 1842.
- Condition assignment `if (w < 3 || (texh = top + 1) < 3)`: 830 (right register, but an assignment-value copy and
  reorg fills the first branch's slot with the subtraction).
- Two ifs (`if (w < 3) return; texh = top + 1; if (texh < 3) return;`): 360, only the first delay slot differs.
- A flag local `bad = w < 3;`: 310 / 905. Every existing local as the difference's holder: it takes that local's
  register (offset `$t4`, rows `$s1`: 310; n 325; texw 350; strip 405).
- `top` as the case-16 clamp written as statements before the command (`top = texw*n - 1; if/?: clamp`): the entry
  matches but the clamp's branch then precedes CALC_DXT's MAX branch (1345); `top = texw*n - 1;` with the MIN in the
  expression: 1445 (the `?:` result is a temporary of its own); `((top = texw*n - 1) < 0x800 ? top : 0x7FF)`: 2945;
  `top = n * hh` in the other case: 920 (that web has no `$v0` conflict).

What is controlled evidence and what is one function:

One function; the scores are the app's judge. The two-edit combination and each half alone are controlled (same seed,
one edit changed). The global.c explanation is read from the dumps, not proved in the compiler source.
