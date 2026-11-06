# Recovering debug boundaries, known-zero masks and narrow captures

These repairs extend the existing conversion/debug families rather than replacing them.
Every proposal still needs the normal exact compiler/link/data judge (or the imported
project's normal judge). They never insert guessed target bytes or alter compiler flags.

## What changed

- `hazard_statement_lines`: a MIPS adapter recognizes a small instruction diff with
  unchanged branch conditions and one-word local destination shifts around a NOP.
  It offers bounded line-boundary changes and same-type local assignment temporaries,
  with branch tails first. A branch witness is a search hint, not CFG equivalence.
- `known_zero_mask`: bounded nonnegative scalar ranges and multiply/shift factors prove
  low zero bits. It tests a redundant unsigned mask at a later shift, or removes the
  existing mask while retaining its unsigned conversion. Unknown writes, aliases,
  signed ranges and conditionally established bounds do not supply a proof.
- `post_call_narrow_capture`: an unescaped local capture can move after a call while
  the call argument retains the explicit original narrow conversion. This extends the
  existing formal-prototype route when that prototype is unavailable. Other arguments
  cannot mutate or escape the value, and no evaluated statement may be crossed.

An unconditional local macro definition no longer disables these narrow families.
Definitions remain opaque; referenced macros in an edited interval reject a proposal.
Conditional directives, source-line controls and line-observing macros remain unsupported.
The view helper keeps source offsets and physical lines intact, including continuations.

The scalar families reach shared draft/local/deep search on supported 32-bit ABIs;
the measured hazard family reaches local experiments, CPU recovery, iterative autopilot
and deep refinement. Missing measured rows or a non-MIPS reader means no hazard proposal.
Other architecture readers are planned. Existing search budgets and judges are unchanged.

## Copied historical replay

Compiler: KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Three frozen precursors of **one already solved rendering function** were replayed on a
private copied export. Source/header hashes, target bytes, compiler identity and exact
control are pinned; each precursor receives 32 candidates and 30 seconds. Reports count
precursors separately from distinct functions. This is not three new live matches.

Direct individual checks reproduce **410 → 140 → 20 → 0**: the proven mask, the
post-call capture, then the hazard/assignment-conversion boundary.

An early implementation exposed a real integration failure: definitions inside the
function sent generation through the macro path, bypassing the new scalar families.
The dedicated early path now preserves those definitions and reaches the repairs.
The initial attempt also sampled temporaries by declaration order and missed the tail;
bounded reverse source order recovered the final conversion instead.

The newer main baseline already includes conversion scheduling families. Compare against
that baseline, not only the earlier build that recovered none. See the measured table
in COMPILER_MATCHING_LEARNINGS.md. These results establish rediscovery effort for this
case, not general runtime or token savings. The live app/project are untouched.

## Repeat the historical gate

Game code and ROMs stay private. Supply the locally frozen manifest and copied export:

```sh
DECOMPNET_HAZARD_REPAIR_SUITE=/private/path/frozen.json \
DECOMPNET_HAZARD_REPAIR_PROJECT=/private/path/copied-export \
uv run pytest tests/test_hazard_mask_capture_repairs.py -q
```

The optional `DECOMPNET_HAZARD_REPAIR_BASELINE` points to the baseline list of singleton
suite reports. Comparison rejects changed source or baseline scope and unequal budgets.
Without private inputs this test skips; public tests still check the conservative
readers, macro handling, mutation/escape rejection and shared search integration.
