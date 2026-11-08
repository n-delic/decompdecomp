# Agent experiments and continuation


Prefer compile_patch for small edits instead of resending the complete C. Call get_candidate once if you lack the current source/revision; then keep your edited source and use the revision returned by each compile. Do not fetch get_candidate after a successful patch unless you lost track of the source or a patch is stale. Use compile_and_diff for substantial rewrites.

## Repair families

Use compile_family for bounded casts, signedness, declaration_order, typed_temporary, branch_orientation, narrowing, source_lines, address_temporary, constant_local, comparison_load, stack_array, loop_induction, parameter_width, value_lifetime, sum_shape or call_result_inline experiments without spelling out variants. Pattern families need only function/revision; source_lines requires known debug flags. Releasing a tried function also runs a bounded local repair pass before another agent edits it. family_targets lists current choices; selector mode=next selects an untried target; anchor mode takes old/occurrence and declaration mode takes declaration. Never combine modes.

## Compiler trials

Use compiler_experiments for up to six independent source/profile trials from the current revision instead of resending C to try_compiler. For C89 compilers keep declarations before statements in each block. Use compiler=current to retain the function compiler. Trials are diagnostic; get_compiler_trial returns the selected C/profile and context ID, not the full project configuration.

## Explicit repairs and batches

Use try_local_repairs before guessing families: it discovers applicable repairs or tries a named families list against one snapshot with at most six compiles. Use compile_patch for small local edits; compile_and_diff is for whole-function rewrites. Wait for each same-function edit response before another edit; separate functions can run in parallel. Batches select the best continuation, keeping baseline on ties; apply selected changes in the response. Missing signedness/declaration_order selectors choose the next untried valid target. Use compile_variants for up to six independent small alternatives. Explicit batch requests always run the supplied variants. Automatic repairs are separate: request try_local_repairs when needed; they never replace a submitted batch. Batch claims hydrate only the first function; load queued functions with get_function(section='brief'). Session context budgets start a fresh session with saved work and experiments.

## Recovery and evidence

Candidate-quality warnings are agent reports, not proofs. Read get_function(section=quality) for saved compiled alternatives before redoing a historical correction. Use report_candidate_issue with the affected revision and optional compiled alternative rather than relying on prose detection. needs requires original direct-call evidence, never a workaround for exhausted work. Quality reports do not pause agents or automatic workers. Use their evidence to repair the candidate; do not repeat an unchanged failed experiment. An Already tried or No untried response means stop that experiment until source or build inputs change. Use a different evidence-backed repair or release with precise notes. Supporting history and notes: get_function(section=history or notes).

## Assembly paging

Prefer get_assembly(focus='mismatch:N') for a later differing instruction, focus='body' for a bounded post-entry page, or focus='callee_symbol' before reading full assembly pages. get_assembly and last_diff are paged.

An exact compiler trial is still diagnostic. Retrieve the exact C/profile pair, then
call `suggest` with that `function`, `compiler`, `flags` and unchanged `source` to save it.
Omit `ranges` for one function: this uses fresh linked-byte/data verification and saves
its source/header/profile together when automatic compiler changes are enabled. Otherwise
it waits for user review. Do not propose or wait for a neighbouring range to save a
verified single-function pair. Broader ranges require separate neighbouring evidence.

## Return and lifetime hypotheses

Local repair search includes `return_contract`, `narrow_expression_inline` and
`plain_float_slots`. Check target-backed return diagnostics before more register
permutations. Caller-backed m2c context is a private draft hypothesis, not a shared
header change. Preserve narrowing on each repeated expression; volatile local storage
changes require target evidence and never extend to MMIO. After repeated identical
output, use a different evidence-backed family rather than resending unchanged trials.

## Hazard boundaries and scalar captures

Use `hazard_statement_lines` with current measured rows when branch tests agree but
local labels shift by one instruction around a NOP. Do not treat branch relocation as
a register miss. `known_zero_mask` and `post_call_narrow_capture` are bounded local
families for proven scalar bits and capture timing; no guessed shared prototype is
required for the explicit-conversion capture. Keep successful source line breaks and
profile/context together. Frozen precursor replay measures recovery; it is not a new match.


A lower native-permuter score is not proof of sound C. In particular, check that
expanding a captured address does not recompute it after an index changes. The
generator guards supported local captures, but other transformations and type
conversions still need review before using a variant as a new search baseline.
Generated inline helpers are private, but check complete build output too: a
function-window match alone does not verify extra helper code or its placement.

## Compiler-mapped storage probes

For a same-frame isolated spill pair, `mapped_spill_storage` tries just the local
identified by current compiled C-line evidence. `mapped_register_lifetime` offers
small same-type lifetime changes for a consistent register-only residual. Both need
fresh build-scoped evidence and source-hash-matched locations; no map means no guess.
Use these through local repairs before broad declaration or source-line sweeps.
The initial held-out cases remain unresolved; a generated proposal is not a promise
of improvement, and the ordinary exact judge remains mandatory.
## Focus, combinations and output evidence

When the ordinary compiler supplies source lines for a mismatch, local variants prefer
nearby copy, divisor/flag and debug-boundary proposals while retaining broad probes.
Locations are associations, not proof of causality. A normalized divisor followed by a
register-only miss is a reason to try `dead_copy_source`; the planner also prioritizes
that pair. Do not change headers or compiler flags to force it.

Boundary search now exposes a bounded pool beyond the first twelve candidates. Deep
and background runs resume their boundary cursor after a scored trial, and iterative
search keeps its existing persistent proposal pages. An interrupted generation does
not advance a cursor. A changed source, header, profile or proposal pool starts afresh.

Equal scores do not imply identical assembly. Feedback distinguishes unchanged output,
changed output without a score improvement, and changed output with improvement. Unknown
fingerprints stay unknown. Repeatedly inert families receive fewer early slots; they
remain available, with exploration and complementary-pair slots reserved. Region advice
names improved/regressed target blocks, not inferred C blocks. Preserve useful regional
progress and inspect regressions before requesting another full assembly dump.

Use get_search_brief(function) before long searches. Explicitly review promising current-profile diagnostics with review_search_seed; changed/unknown interfaces remain diagnostic-only even when exact. Review callers and submit normally. Never add dummy uses.

For a result-preserving target exit, get_search_brief can suggest a diagnostic return/lifetime pair and list current failed trials. Deep search can generate supported pairs without a supplied seed, even if the first edit regresses; interfaces still need review. Allocation-mapped middle quotient sites are eligible. Scope-depth estimates are heuristics; judge actual output and do not add dummy uses.

For a near match whose return-value copy rotates around address setup, start with
`try_local_repairs` families `prepared_return_scope`, `prepared_call_scope`, and
`assigned_call_scope`. Deep search prioritizes the same bounded lane automatically.
These retain the real call and ABI; do not replace them with dummy reads or fixed
registers. If moving the copy fixes order but swaps registers, read
`get_search_brief` for cached refs/live-length evidence before more flag searches.
Reading the packet does not compile. Deep search may capture diagnostics only after
trying the cheap call lane, inside its existing compile/time budget, and requires
ordinary/diagnostic object parity. Missing capture evidence is not exhaustion.
Read `get_function(section="brief")` for a compact structural comparison with a
matched neighbour, including donors too large to inline. Differences are leads,
not permission to copy its prototype, dead uses, or side-effect ordering.

When the body and local spills agree but the frame is uniformly too small, read the
frame-only packet before requesting more assembly or trying padding/flags. It lists
actual unsigned-halfword store sites and `halfword_store_normalization`; use that
family with `try_local_repairs`. Deep and iterative search try the same finite page
first, within their existing budgets. A larger-score body-correct alternative can be
a better repair root than a low-score source with misplaced local spills: both are
retained, but only ordinary exact judging accepts a match. Check the current-input
failed trials before repeating variants. Unknown store widths are not inferred.
