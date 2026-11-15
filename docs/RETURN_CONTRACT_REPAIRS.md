# Return contracts before register permutations

A copied N64 GCC case required three complementary source changes: plain local float
slots, an explicit returned index, and inlining a repeated byte expression. The fixed
profile was KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.

## Automatic repair families

- `return_contract` checks an explicit straight-line target return-register copy.
  It proposes returning an existing initialized scalar from a void candidate. Shared
  declarations and recursive calls require coordinated review and are left alone.
- Caller consumption of that result adds a brief diagnostic. Where no shared prototype
  exists, m2c receives a private return-type hypothesis; it does not edit the header.
- `narrow_expression_inline` substitutes a bounded pure repeated expression while
  preserving its declared byte/short conversion at every use. Mutation, escapes,
  unknown macros, pointer loads and control-flow crossings reject the hypothesis.
- `plain_float_slots` replaces unescaped volatile local float temporaries with ordinary
  array storage only when target call-delay-slot stack-store evidence exists. It never
  removes volatility from globals, pointers or MMIO.

These are candidates, not equivalence or matching proofs. Normal compilation and exact
linked-byte/data checks (or the imported project's normal judge) remain mandatory.
Repeated identical output redirects some search slots toward these families after three
observations. Third direct probes and periodic ordinary exploration remain available.
Complementary repairs retain the existing search depth and candidate allowance.

## Controlled copied-project replay

Against main `f877d206`, with identical frozen source/header/target scopes and a
32-candidate / 30-second allowance per precursor:

| Starting score | Main first exact trial | New first exact trial |
| --- | --- | --- |
| 350 | None | 6 |
| 190 | None | 2 |
| 65 | None | 4 |

These three precursors are **one already solved function**, not three new live matches.
The initial implementation reached score 65 from 350 but failed to finish within the
budget. Prioritizing applicable semantic families and their complementary continuations
recovered the full sequence without increasing the budget.

Across eleven precursors of six distinct functions, recoveries rose from five precursors
of two functions to eight precursors of three functions. All eight older precursors
retained their previous best scores and first-exact trial numbers. The remaining scores
were 550, 60 and 230. The 790-starting case exhausted its time allowance after 15 main
trials versus 11 new trials; both reached 550. No general elapsed-time or token reduction
is established. No live project was changed or full ROM rebuild claimed by this replay.

Public tests cover the real installed m2c return-context behavior when available,
synthetic shared ROM/imported deep-search chains, safety rejections and search fairness.

## Reach and remaining gaps

Pure narrow-expression repair is shared across supported 32-bit integer ABIs. Return
and delay-slot readers currently require MIPS target assembly; unsupported architectures,
multi-exit control flow and missing imported target/caller adapters yield no evidence.
They remain planned work rather than inferred contracts. Existing shared prototypes
always take precedence over a generated draft hypothesis.
