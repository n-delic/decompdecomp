# Compiler-mapped storage and lifetime repairs

## Narrow evidence before source changes

`mapped_spill_storage` requires a complete MIPS diff with equal stack frames and exactly
one differing word-store/load pair: same value register and consistent old/new aligned
slots inside the frame. Return-address, stack-pointer and global-pointer slots are excluded.
`mapped_register_lifetime` requires a small register-only residual with a single consistent
substitution and identical non-register operands. Neither reader supplies an exact verdict.

The current compiled object must also associate a differing instruction with a source line.
That mapping is bound to the exact source hash. Its assignment's destination identifies a
local to try; it is an association, not proof of the original variable or register choice.
Missing/stale mappings produce no targeted candidates. No instruction index is guessed to
be a C line, and no trial fixes a physical register or emits assembly.

The storage family tries a bounded set of placements for just that local's declaration and
one-element local array storage. Placements stay in the initial declaration run and never
move evaluated initializers. The register family also offers same-type assignment-conversion
temporaries with a short lexical lifetime, evaluating the original RHS once. Qualifiers,
escapes, shadows, hidden macro references and line-observing constructs reject unsafe cases.

Shared local experiments, CPU follow-ups, iterative autopilot and deep refinement consume
these proposals. Agent baseline and normal permuter evidence retain the compiler mapping
already computed from the actual object. Imported diagnostic mappings are retained too.
Unsupported object/debug-line formats and non-MIPS residual readers decline; adapters remain
planned. All saves still require the normal compiler/link/data or imported-project judge.

## Trial selection

Broad `source_lines` and `debug_statement_boundary` families no longer lead a residual with
no scheduling, allocation-scheduling, branch-target or delay-slot evidence. One family in an
eight-family window remains available for exploration; other families are retained by the
existing rotating cursor. Strong scheduling/control evidence preserves the established order.
Target-backed hardware families still precede generic hypotheses. No budget is increased.

## Copied remaining-function sweep

Baseline main `3b107ab7`, 22 frozen open functions, identical sources, headers, target bytes,
compiler identities and per-function profiles. Both runs received at most 32 candidates and
30 seconds per function; in-flight generation/compilation can complete after the time cutoff.
Two local workers per build ran concurrently with regression tests, so wall times and trial
counts are resource-dependent observations, not a timing benchmark.

| Measurement | Main | With targeted repairs/selection |
| --- | ---: | ---: |
| Evaluated functions | 22 | 22 |
| Candidate trials | 419 | 419 |
| New exact matches | 0 | 0 |
| Score improvements | 0 | 0 |
| Broad line-family trials | 96 | 80 |
| Targeted spill trials | 0 | 2 |
| Targeted register trials | 0 | 2 |

The line trial reduction is 16 observed attempts, not a reduction in total compiles or proof
of token savings. Time limits prevented many cases from using all 32 candidate slots. This
comparison must not be presented as the earlier 704-trial sweep. Equal-budget/source scope
checks pass, but it is not an equal-compile-count ablation per function.

The closest stack case has 251 instructions and equal 96-byte frames. Only one store/load
pair differs (52 versus 20 bytes from SP). Seven direct targeted proposals all compile but
do not improve its score of 2. The 30-instruction register case has four operand differences;
its mapped same-type temporary does not improve score 20. They remain unresolved.

Additional failed experiments: register qualifiers, pointer/integer spellings, one-element
array storage, moving the declaration to the first assignment, changing unused padding and
union storage did not solve the stack case. Splitting a pointer chase, staging its comparison
constant and rewriting its early return did not solve the register case. Some alternatives
made more instructions differ. These observations motivate bounded hypotheses, not broad
transformations enabled without evidence. No live project was changed or cartridge rebuild
claimed. Game source, ROM and private replay inputs stay outside the repository.

The compiler is KMC GCC 2.7.2. Nineteen cases use `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Three retain their saved no-debug, fast-math or G8/strength-reduction profiles. No settings
were changed and historical saved scores were not treated as fresh judge results.
