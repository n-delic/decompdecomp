# Replay captured repair examples

## In the app

Open **Agents → Repair examples** and use **Test automatic repairs**. It tests up to 20 examples
matching the category/review/project filters. Each expanded card also has a single-example action.
Choose 1–32 trials and 1–120 seconds per example; defaults are 12 trials / 30 seconds. Progress and
results appear in the dialog; **Stop replay** cancels compiler work through the shared job system.
Results are compiler-output regressions, not adopted game matches, and do not mark examples implemented.
Imported projects or incomplete historical context are reported as skips.

## Offline

### Check transfer on distinct frozen functions

Use `frozen_repair_cases.select_holdouts(items, development_functions, count=6)` to
select distinct functions by a stable name hash, excluding the examples used to
develop a rule. Freeze sources, headers, targets and compiler identities before
testing policies. The helper does not inspect candidate outcomes; historical
human investigation may still limit how independent the sample is.

For a private exported ROM build and a frozen case manifest, compare identical
budgets with named families omitted **before paging**:

```sh
uv run python -m decompnet.app.linked_repair_benchmark \
  --project /private/copied-build --cases /private/frozen-cases.json \
  --trials 32 --seconds 30 --omit-families parameter_carriers,narrow_load_subtraction \
  --output /private/control.json
uv run python -m decompnet.app.linked_repair_benchmark \
  --project /private/copied-build --cases /private/frozen-cases.json \
  --trials 32 --seconds 30 --reference /private/control.json \
  --output /private/enabled.json
```

Reports keep failures, first exact checks, winning sequences and compatible-output
duplicate counts. Candidate checks are not compiler-process counts: cached inputs
can avoid work, while a baseline or multiple profiles add compiles. Deep search
also records actual compiler starts and the count when first exactness is observed,
including speculative work. These process totals are explicitly pool-scoped;
a sweep's shared pool is not an individual function's compile count. Saved deep-pass results keep bounded family counters;
live progress shows compact repeated-output totals. Parallel duplicate windows are
lane-local, and unknown fingerprints are never treated as distinct output proof.
These deterministic searches use zero model tokens; paid-agent usage still comes
from provider telemetry. No replay result adopts source or changes a live project.

Use replay to find out whether the app's current deterministic families can reproduce a captured repair.
It uses no model requests, opens no project database and does not apply candidates or change review states.

1. In **Agents → Repair examples**, select the cases to investigate and **Export full examples**.
2. Keep this private export outside the repository: it contains game C and headers.
3. From a checkout, run:

```sh
uv run python -m decompnet.app.repair_replay /path/to/repair-examples.json \
  --output /path/to/replay-results.json --limit 20 --trials 24 --seconds 120
```

Use `--ids 123 456` to select specific cases. Limits are per example; baseline/control compilation and
the current trial can finish after the soft deadline, with normal compiler subprocess timeouts still active.
No toolchains are downloaded. Install the captured compiler through the app first if necessary.

The report checks source/header hashes, identical captured compiler/build settings, and a reproducible baseline/control
difference. Captures differing only in judge revision can also be recompiled under the same compiler/header;
the report and dialog explicitly label that case, and historical scores are not compared. To inspect one
in the app, clear **Comparable source repairs only** and use its single-example replay. Other settings
or header changes still block replay. Missing historical C or context, changed settings and uninstalled tools are reported as skips.
Candidate families rotate, one candidate per family per round, so a large family cannot spend every trial.

Results include `compile_error`, `improved`, `neutral`, `regressed`, and `control_output_recovered`.
The last means the normalized function fingerprint, all allocated sections (including helper code and BSS sizes), and named relocations equal the compiled
successful control. It is **not a newly verified game match**: the export does not carry a complete ROM memory
map or an imported project's build and objdiff objects. Debug information is not part of this comparison.
Use the original project's normal linked-byte/objdiff judge on a copy before adopting a proposed fix.
Without edit minimization the report stores hashes and bounded diagnostics. Enabling minimization also includes the resulting private C.

The shared runner also accepts a diagnostic measurement callback for copied project integrations.
Standalone imported-project replay currently skips cases because full exports do not capture the complete
build system and historical headers; a guessed compiler profile would give misleading results.

## Current evidence

Random-rewrite labels now validate the recorded candidate hash. A label inherited while a successful
candidate triggers more matches is retained as origin information, not treated as the destination's
rewrite. Reports additionally distinguish whether the recorded base hash equals the failed baseline.
The last rewrite name is not sufficient to isolate the change that caused a match.

A private audit found eight of 444 random-labelled captures with matching candidate hashes; only two
also had the recorded failed baseline. One was already recovered by `table_slot_lifetime`; the other
motivated bounded `member_store_replay` proposals. These are snapshot counts, not an estimate of model
or permuter effectiveness. Existing capture/review records were not deleted or reclassified.

Four private captured regressions were recovered by `pointer_chase_reuse`,
`independent_assignment_order`, `terminal_early_return`, and `member_store_replay`. Separate normal
linked-byte checks accepted a generated candidate in each case. The first three used KMC GCC 2.7.2;
the member-store case used IDO 5.3. These recover existing matches, not additional game progress.

A private same-context floating-point case used KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The product-intermediate family recovered the successful control;
two alternatives improved regression score 180 to 35 and 55. A separate normal linked-byte check in a
private project copy accepted one candidate exactly. This recovered an existing match, not new game progress.

Three other private cases produced no control recovery in a 12-trial allowance (one had only nine
applicable candidates). These neutral outcomes are retained so wider search is evidence-led.

## Minimize a captured fix and inspect GCC phases

The Repair examples replay panel now includes:

- **Minimize captured fix first**, checked by default. Up to half the existing trial budget reverts
  individual changed line hunks. An edit is removed only if the successful function output and all
  allocated object sections/relocations still agree. Earlier removals are retained while later hunks
  are tested. This is bounded ablation, not a proof of semantic necessity or a globally minimal fix.
- **Capture GCC phase summary**, off by default. At most two additional local compiles add `-da` to
  the captured failed and successful sources. This requires an already installed native GCC driver;
  unsupported compilers/adapters are explicit skips, and nothing is downloaded. Each diagnostic output
  is compared with its normal compile before register/phase evidence is considered comparable.
- **Download analysis report** after a run. The local report includes the minimized C, retained edit
  indexes, per-trial outcomes, and bounded phase summaries. Treat it as private game-source evidence.

The phase summaries list captured phases, pseudo counts, reference/lifetime/call-crossing statistics, pseudos disappearing between flow and
combination, empty conflict lists, selected allocations and truncation markers. These are investigation
leads, not proof of why a spill exists. Full raw dumps are not retained or sent to an AI service.
The diagnostic compiles share cancellation and compiler resource limits; they can consume the remaining
replay time. Analysis never applies C, changes compiler settings or marks a game function matched.

The same controls are available offline:

```sh
uv run python -m decompnet.app.repair_replay /private/repair-examples.json \
  --output /private/analysis.json --trials 24 --seconds 120 --minimize --diagnostics
```

Scalar failed-source penalties are now scoped to the exact parent C as well as its build inputs.
Changing source permits relevant families to be explored again. Exact failed compiled candidates remain
cached under their existing build context: retrying a family does not mean recompiling identical input.

## Benchmark multi-step repair search

In **Test automatic repairs**, select **Benchmark repair combinations**. This turns off edit
minimization; selecting minimization turns benchmarking off. The same option is available offline:

```sh
uv run python -m decompnet.app.repair_replay /private/repair-examples.json \
  --output /private/combination-results.json --trials 128 --seconds 120 --combinations
```

This uses the autopilot beam (width three, depth four), round-robin candidate families, and the
same per-example trial/time budget across every step. Up to three measured structure improvements
can each receive two immediate follow-up probes; the rest of the wave stays queued. Search stops
on recovery of the full frozen control output. Reports show candidate compiles separately from
the two baseline/control compiles, elapsed wall time, first recovery trial, family path, pending
work and stop reason. Optional GCC diagnostic compiles are additional, as described above.
The single-step runner also reports compile count, first recovery trial and elapsed time so it
can serve as a comparison. Use identical evidence, tools, budgets and machine conditions; timings
are observations, not promised speedups. Compare failed cases as well as successful ones.

The path records generator families, not atomic semantic edits: one family can itself propose a
bundle. Source hashes identify the actual candidates. Region preservation, equivalent-output
pruning and path ranking only guide exploration. Only full control-output equality recovers a
replay; normal linked/objdiff checks are still required before adopting any game change.

Deep search now retains bounded equal-score alternatives as well as worse alternatives. Cached
local near-misses retain their own diff rows so a later run can explore an untried second edge
without recompiling the same first edge. Numeric-only historical cache entries still skip known
misses but do not supply invented structural evidence. Compiler/header/target/build scope changes
invalidate reuse. Failed or unscored builds remain retryable; they are counted separately.
Autopilot persists pending sources, family paths and visited edges in its existing scoped frontier.

Deep activity and autopilot notes distinguish budgets, supported-variant/depth exhaustion,
failed/unscored trials and pruned alternatives. No bounded search exhausts every possible C program.

## Read-only linked-ROM benchmarks

For an exported MIPS ROM build, a private starting source and its frozen header can be tested
against the original image rather than a compiled control:

```sh
uv run python -m decompnet.app.linked_repair_benchmark \
  --project /private/exported-build --function FUNCTION \
  --before /private/starting.c --header /private/context.h \
  --output /private/linked-results.json --trials 128 --seconds 120
```

The image SHA1 and assembly target bytes must agree. The recorded registry compiler must already
be installed. Exported custom commands are ignored. The tool never runs the project's build
scripts, writes its files or database, adopts C, or downloads tools. Reports record input hashes,
pinned compiler identity, candidate compiles, first exact trial, elapsed time and family path.
Only the ordinary linked-byte and ROM-data judge supplies an exact result. Physical linked
instruction rows guide regional search without residual relocation-rendering differences.

Use `--families initialization_cluster,balanced_pointer_calls,allocation_initialization_order`
to isolate a combination. Limits are 1–4096 candidates and 1–1800 seconds; the current compile
and candidate generation may finish after the soft deadline. Keep all source/header inputs
and reports outside the repository. Compiler failures count against the candidate allowance;
the baseline is measured once and reported separately in `compiler_invocations`.

The shared C families also reach supported imported projects. This read-only exported-ROM
adapter currently needs MIPS targets; other processors and imported builds can use the existing
copied-evaluator replay interface. This limitation does not disable their ordinary local repairs.

### Target-block composition and captured subsets

Iterative search interleaves family rankings from the first differing 16-instruction
target block (with adjacent context) and the whole function. This ranks hypotheses;
it does not map C lines to assembly or prove that an edit preserves a correct block.
The frontier prefers less damage to previously correct blocks, then regional recovery.
Up to three regional follow-up bursts move two child probes forward within the same
compile/time allowance, width and four-stage depth. A worse scalar score remains
eligible only under the existing eight-times ceiling, equal instruction count and
measured regional recovery. Pending candidates remain resumable. Ordinary judges
alone accept matches; displayed rows never do.

Captured-output minimisation spends at most half its trial budget testing interleaved
single-edit and pair subsets from the failed source. A successful subset becomes the
starting set for greedy deletion. `sufficient_recipes` records edit indices that
reproduced the frozen successful output, not semantic necessity or an automatically
enabled repair rule. The total compiler budget is unchanged; short budgets cannot
exhaust every pair. Agents also receive the first differing target block and compact
register/scheduling/stack clues. These are advisory, not proven lifetime conflicts.

## Proposal pages and productive follow-ups

Iterative repair stores a cursor for each exact parent source and ordered generated proposal pool
inside its existing build-scoped frontier. Autopilot takes eight targeted candidates per page,
then interleaves the other candidate sources as before. Omitted generated candidates remain deferred;
a later wave/run can reach them. Restarting restores the pending work and cursor together. Changed
source, proposal ordering/pool, compiler, header, symbols or rules cannot reuse an unrelated cursor.
This covers generated candidates, not every possible C program or candidates excluded by a generator's
own safety limits. An empty page containing already visited candidates remains resumable. A two-parent, input-keyed
in-memory proposal cache avoids regenerating the same pool within a visit; it never caches a verdict.

A strict numeric improvement with usable diff feedback can earn two immediate follow-up slots,
up to three bursts per function's saved search, within the existing phase budget/depth limits.
Existing structural-gain bursts remain separate; the numeric rule does not duplicate those bursts.
Known paired-operand/affine-counter combinations receive up to sixteen early positions, reserving
at least two page positions for other available families (six preferred positions on an eight-item page). Only two children move ahead
of the ordinary queue, and all compiles still spend the same phase budget.
Ordinary queued work is preserved, and exact adoption still comes only from the normal judge.

For operand staging, changed memory displacements can prioritize explicit pointer-offset sites and
scratch pairs that can safely cover those sites. This is an advisory overlap, not a source-line or
register-variable map. Unknown array stack locations keep ordinary ordering. CFG liveness and the
existing side-effect/type checks still gate candidates; whole-function alternatives remain available.

For multiple frozen cases, `repair_benchmark.batch(..., generate_report=True)` can pass the measured
parent report to a generator taking `(case, source, report)`. Return a `repair_selection.Page` to
preserve pagination; store its cursor dictionary with your private replay state. The default two-argument
generator remains supported. Compare reports using `repair_benchmark.compare(before, after)`:

- Record the same per-case scope (target, compiler, header, symbols and ROM/build input identity), starting
  source and equal trial/time limits. The comparator rejects different scopes, duplicates, source hashes,
  baselines or budgets rather than reporting a misleading speedup.
- Results include new/lost recoveries, score improvements/regressions, unscored cases, candidate compiles,
  first exact trial and observed elapsed time. Failed cases must remain in the comparison.
- A recovered historical checkpoint is rediscovery; an unresolved live function improved on a private copy
  is not adopted into the user's project by benchmarking. Keep private source, ROMs and reports outside git.

### Parent-specific unchanged output

Iterative search now records unchanged code/relocation fingerprints for each exact parent source
and repair family. After two unchanged outputs it defers that family's queued siblings. It keeps
all candidates and reserves one in three scheduling slots for deferred exploration when both queues
have work. A different parent gets fresh evidence; changed output resets the consecutive count.
Displayed scores/rows, failed compiles and missing fingerprints do not establish unchanged output.
The frontier's complete build scope and repair implementation hashes invalidate stale evidence.

An improved, still register-only near miss can also earn one installed-GCC phase comparison per
frontier. This needs four available compiler slots and an 80-second deadline margin. Only actual
compiles spend those reserved slots; unsupported adapters release the unused reservation. The dumps
and comparison remain advisory, stored on the frontier with a short activity note. No diagnostics
run for exact matches. Diagnostic builds never adopt code, accept a match or change settings, and
phase comparisons require `-da` to preserve each normal object's allocated code, data and relocations.


Combination replay uses the same paged, mismatch-guided candidate selector as iterative
search, including scope/value complements and bounded allocation detours. Reports include
candidate compiles, first exact trial, winning family path, elapsed time,
`allocation_followups` and `no_effect_families`. The latter counts parent/family groups
with two identical measured outputs, which move behind fresh families without being
removed. Replays compare against the frozen successful compiler output; they do not
adopt C or establish a new linked-byte game match.

## Compare a frozen near-match suite

`linked_repair_benchmark.suite(copied_root, cases, trials=16, seconds=30, focused=True)`
benchmarks a list of `{name, source, header}` cases from a private copied ROM export.
Run again with `focused=False` and compare the reports with `repair_benchmark.compare`.
Every case gets the same candidate/time budget. Each baseline is recompiled and must
remain unresolved. Scope hashes cover the copied manifest, target, header and pinned
compiler identity. Results contain hashes and measurements, not source text.

The focus is advisory: explicit symbols and numeric operands around the first mismatch
rank overlapping source edits. There is no invented mapping from registers to C locals.
Only 128 proposals are inspected; every fourth position reserves ordinary exploration,
and no proposals are deleted.

A six-case MIPS/GCC near-miss snapshot was tested with 16 candidates and 30 seconds per
function. Both orders used 96 candidate compiles and produced **zero new matches or
improvements**. Search time was 33.2 seconds without focus and 32.2 seconds with focus;
that difference is timing noise, not evidence of a speedup. Fresh linked baselines,
not historical displayed percentages, were used for comparison. Larger budgets or new
repair families may be needed; this result does not establish general effectiveness.

### GCC value traces, private unit context and promotion gates

Finish near matches can capture one initial native-GCC value trace for a register-only baseline,
including a cached baseline. It reserves two compiler slots and a 40-second deadline margin from
that pass's existing allowance; unsupported adapters release unused reservations. This does not run
an AI agent. Ordinary autopilot does not pay for the extra initial trace. Existing improved-candidate
phase comparisons remain separately capped. Stop, per-function quotas and the exact judge still apply.

Complete bounded RTL/flow/global-allocation dumps supply instruction IDs, definitions/uses,
`REG_DEAD`/`REG_EQUAL` notes, explicit calls-crossed counts, source-line notes and register dispositions.
Only the requested function is joined within one compilation. A physical register reused for several
pseudos gives several candidate values, not an inferred C variable or an assembly-address map.
Diagnostic flags must preserve normal allocated output. Source/header/profile hashes, truncation and
user line directives constrain reuse. Full dumps stay private; stored summaries are bounded.

A six-candidate bundle pool combines an existing lifetime hypothesis with a disjoint operand/scope
edit near explicitly traced lines. It uses original-source spans, rejects overlapping physical lines
and spends the normal search budget. The same stored trace can supply `value_trace_bundle` to local
repair batches without recapturing it or adding a provider request. Changed settings invalidate it.

`verified_unit_helpers` reads at most 24 current exact donors in the recipient's known unit and
compiler profile. It transfers at most four unambiguous static helper/simple static-const scalar
bodies into a private candidate namespace. It never manufactures a definition from an extern,
changes shared headers, or assumes a private calling convention. Unknown units, stale verification,
regressions, conflicting bodies and identity-sensitive helper uses are rejected. The recipient is
always compiled/judged again. Imported-project unit/verification adapters, object arrays, original
helper line fidelity and full translation-unit/clobber experiments remain planned.

New bundle/context families cannot earn an adaptive boost without a current replay approval. Broad
first-difference reordering also requires approval; established operand-supported ordering remains.
The review tool is:

```sh
python -m decompnet.app.repair_promotion training.json heldout.json --output decision.json
```

Each envelope contains `profile`, `family`, `rules`, `control`, `treatment` and `groups`: the reports
use the ordinary equal-budget `repair_benchmark` format, while `groups` maps each frozen case ID to
its target identity. All checkpoints of a target must share its group. Training and held-out target
groups must be disjoint and both envelopes must isolate the same family/profile/rules. Review the
compiler evaluator and frozen-input provenance; caller-supplied metadata is not independent proof.

Promotion needs an exact-recovery/first-exact compile improvement in **both** splits, no increased
total compile cost, no lost recovery, score regression or unscored case. Score-only gains and timing
noise cannot promote. The CLI emits a source-free decision and exits nonzero for a rejected gate;
it does not change app policy. `repair_promotion.install(net, training, heldout)` is the explicit
reviewed developer import. Rejection revokes an earlier approval; compiler/profile or repair-rule
changes prevent reuse. Candidates remain available for ordinary exploration without a boost.

The residual-cohort investigation added one target-backed independent-address family.
An equal-budget on/off replay of six frozen cases (96 candidates per run) improved one
case from linked score 155 to 25, with no score regressions and no new exact matches.
Five register-only cases remained unchanged. See COMPILER_MATCHING_LEARNINGS.md for the
controlled settings and failed probes; timing changes were not a demonstrated speedup.


## Recover historical exact source

For local ROM projects, **Recover saved matches** beside Finish near recompiles saved exact C,
including verified bundles across function renames and unit changes. It checks the target bytes,
relocations, ROM/architecture identity, then tries current and recorded headers. Legacy same-function
score-zero attempts are only hypotheses. Every restoration requires the ordinary current linked
code/data exact verdict, a held claim and atomically unchanged source/context/configuration.
Old scores never establish progress. This explicit, cancellable pass has its own selected time/compile
limits; misses are input-scoped, tool failures remain retryable, and candidate cursors rotate between
runs. Imported builds need archived build inputs and are not supported by this pass.

Minimization reports now include a narrow `recipe_candidate` when the token change can be expressed
by the existing expression grammar. It is a review candidate, never automatically enabled.
`minimized_recipes.propose` checks failed/successful controls and reverifies the minimized result;
its ablation budget excludes the three possible control checks. `validate` reserves two compiler
trials per applicable independent target: an unmatched scored baseline and its freshly exact rewrite.
Training and held-out groups must be disjoint. Only an explicit reviewed `learn` import with the same
profile and recipe digest can store it. Caller-provided groups/judges require provenance review;
this is a developer gate, not a trusted receipt from arbitrary JSON. All subsequent uses are judged.

GCC diagnostic comparisons align values only at unique unchanged token-line anchors under the same
header/profile, with unchanged diagnostic output. Pseudo IDs are compilation-local. Multiple values
at an anchor are omitted, not guessed; changed registers/def-use counts are advisory.

MIPS iterative/frontier region scoring uses decoded target-text control-flow blocks and explicit
within-block register chains when supported, keeping delay slots with branches. It declines unknown
transfers, out-of-range destinations and incoming delay-slot edges. Other architectures and unsupported
MIPS streams retain the fixed 16-instruction fallback. Calls/unknown operations clear dependency state;
there is no memory-alias or cross-block value proof. These hints affect bounded search retention only.

## Isolate local-pair and output-diversity search

`repair_benchmark.run(..., paired_search=False, output_diversity=False)` supplies explicit developer
ablation switches. Defaults enable the bounded paths used by iterative autopilot. Keep source,
compiler/profile, headers, target groups, per-case trial and time budgets identical when comparing
main, pairs-only, diversity-only and the combined treatment. Pairs are composed from freshly scored
non-improving siblings, spend ordinary candidate slots and never accept a score-only result.

Reports now include `residual_diagnosis`, `search_policy`, `paired_proposals` and `paired_evaluations`.
Queued proposals are not claimed as evaluations. Evaluations can use an input-scoped cached verdict; they are not automatically fresh compiler invocations. The comparison records recovered **cases**, total
candidate compiles and first-exact compile savings for shared recoveries. Several checkpoints of
one function still count as one target for the separate held-out promotion gate. Timing alone is
not a win. The existing reviewed promotion workflow remains the gate for learned priority boosts;
adding bounded hypotheses does not approve a learned boost or weaken later destination judging.


## Macro coverage and discovery measurements

Pairing now accepts unchanged simple defines only when all edit hunks and the complete combined
window avoid the existing protected macro regions. Header dependencies participate too. Replay's
`pair_coverage` describes supported/opaque coverage without exporting source. Unsupported directives
remain unsupported; no preprocessor is run and no macro text is rewritten.

`repair_generation.measurements(net)` and authenticated `GET /api/repair-generation-metrics` expose
the last 64 generation sessions in this process, across projects, with no source, prompts, function
names or scope IDs. The history resets on restart. Fields distinguish generated wrapped stages,
cache hits, non-double-counted stage wall time, total session wall time, and first eligible candidate.
First eligible is a filtered proposal, not a compiled or matched function. `Pools.generation_metrics`
provides the same completed session data to developer callers. These counters do not cover unwrapped
work, token consumption or compiler execution time. Cold/warm benchmark timings must account for
compiler configuration separately when measuring the time to its first compile call.

## Register-cycle lifetime search

The deep permuter recognizes closed MIPS register cycles (for example S4/S5 exchanged) only
when ordered physical linked instruction forms agree. Relocation-rendering annotations do not
hide such a diagnosis. This changes search advice, not the score or linked-byte/data verdict.
Before broad mutation it prioritizes at most 24 applicable lifetime proposals. These share the
run's existing deadline and compiler limit. Ordinary local repairs remain available afterwards.

Already installed native GCC drivers can supply actual `greg` allocation order/dispositions
joined to `lreg` reference counts and live lengths. Diagnostic `-da` output must preserve the
normal object's allocated sections and relocations. A capture costs two compiler runs; deep
search attempts at most two captures and reserves a subsequent judge slot. Source, header,
target, symbols and profile scope invalidate cached evidence. Input changes during capture
prevent publication. Briefs read cached evidence only; they never launch compiler tools.
Several pseudos can share a hard register or C line, especially narrowing conversions, so these
records are candidate-side leads, not original variable identities or an exact rewrite recipe.

The new `existing_identity_lifetime` family relocates only a recognized, already-present block
that computes unsigned zero and adds its zero product to an unchanged scalar. It checks concrete
word/halfword typedefs, initialized unescaped scalar reads, scratch use, scope and statement
boundaries. It does not introduce new cancellation expressions, guessed types or machine
register bindings. Unknown syntax/widths, volatile aliases, escapes and unbraced insertion
points are declined. Every proposal still needs the ordinary judge.

### Controlled private replay, 3 November 2026

Eight frozen inputs used the same ROM-backed evaluator, header, target and pinned KMC GCC
2.7.2 profile (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`). Both branches had 16 candidate slots and
30 seconds per input; main was `792b0a5`. Five inputs were successive checkpoints of one menu
renderer, not independent games; three were other functions from the same game.

| Result | Main | Treatment |
| --- | ---: | ---: |
| Existing matches recovered | 0 | 1 |
| Candidate compiles | 128 | 113 |
| Compiler invocations, including eight baselines | 136 | 121 |
| First exact candidate for the near-final identity-placement case | none in 16 | 1 |

The four earlier menu checkpoints and three other functions had unchanged best scores and no
recovery on either branch. The successful frozen baseline scored 155 under this linked-row
adapter; historical app scores are not directly compared. No live project was changed, no
model was run, and this is recovery of an existing match, not additional game progress.
This small same-game cohort does not establish generalization or model token savings.

A separate isolated single-function app-engine copy exercised the real deep-search path:
normal baseline score 275, then score 0 and matched status after one repair. Its four compiler
invocations were one baseline, two unchanged-output diagnostic captures and one repair;
local elapsed time was about 3.7 seconds. Its compact evidence identified the two competing
pseudos and retained ambiguity for another value sharing S4. The global compiler profile was
kept fixed during the measured register-cycle lane. This is a near-final regression, not an
end-to-end recovery of all preceding source reconstruction work.

### Freeze a harder comparison cohort

Select cases using recorded pre-search facts, rather than choosing successes after
trying the new policy. For example:

```sh
uv run python -m decompnet.app.repair_replay /private/repair-examples.json \
  --output /private/hard-results.json --combinations --trials 24 --seconds 120 \
  --limit 8 --min-failed-score 50 --min-instructions 50 --distinct-functions
```

The thresholds use captured failed scores and instruction counts; the native
compiler replay can reproduce a different score against its successful control.
The output records the selected IDs and before/after source hashes before compiling.
Use the same frozen inputs, compiler/header/target controls and budgets for each
policy, and retain unresolved and skipped cases in the report. Report candidate
compiles and control recoveries separately from real linked/objdiff game matches.
A small output-control benchmark does not measure model token savings.

Search scheduling now distinguishes output novelty from useful progress. Fresh
score improvements or bounded regional recoveries without new region damage get
priority. Six nonproductive trials across three families can trigger a strategy
switch even when each trial changes compiler output. Unknown/cached evidence does
not teach progress, and untried/deferred hypotheses retain exploration slots.

For supported MIPS storage residuals, current source-bound compiler-line mappings
can rank CFG-dead local scan temporaries associated with a later mismatching phase.
This is source/liveness advice, not a C-variable-to-register mapping. Missing/stale
maps retain the existing ordering; the original probes remain available alongside
the guided candidates, under the ordinary search budget and exact judge.

The initial harder-cohort comparison used eight frozen captures and at most 24
candidate trials per case. Both policies recovered two controls and reached the same
best scores. Candidate compiles were 164 before and 160 after; regressed trials were
78 versus 53, with compile failures increasing from two to five. One recovery took
nine trials instead of six, while another took seven instead of fourteen. Keep these
mixed outcomes visible; fewer bad proposals alone is not a new exact match.

A subsequent investigation of that same eight-case cohort fixed required-argument
drops hidden by unrelated macros and invalid C89 narrow-store temporary placement.
It also recognized a missing zero-subtraction spelling for an existing scoped
normalization repair and moved applicable structural families earlier. Recoveries
increased from two to three controls, candidate compiles fell from 160 to 132 and
compile failures from five to zero. These are development/regression cases, not a
fresh held-out evaluation. The six unresolved captures received bounded edit
ablation; coupled prototype, qualifier and store-order changes remain evidence for
further investigation rather than automatically learned rules.


### Original image container versus judging inputs

Repair capture and offline replay share one settings comparison. For complete ROM
compiler-invocation snapshots with the same normalized image SHA-1, the original
container `byte_order` is provenance, rather than a compiler change. Machine
`big_endian`, compiler/flags/command, language, context/unit, scoring revision and
all other settings are still compared. Missing hashes, incomplete snapshots and
imported-project builds retain strict comparison.

New captures can select a failed attempt across this container-only difference,
without altering either captured snapshot. Direct replay of an older full export
can also recognize this case and reports a comparison note. Historical stored
review flags and categories are not bulk rewritten: older examples may still be
excluded by their saved “source changes only” filter flag. Replay still validates
source/header hashes and recompiles controls; it never adopts a new game match.

### Freeze a broader sample before candidate trials

`--holdouts` selects distinct function names by a stable hash, excluding the listed
development cases. Selection happens while freezing, before candidate outcomes:

```sh
python -m decompnet.app.linked_repair_benchmark --project /path/to/copied-export \
  --cases /private/candidates.json --freeze --holdouts 6 \
  --development-functions development_function --output /private/frozen.json
```

Replay that frozen manifest with equal budgets, then repeat with
`--omit-families scratch_role_reuse` for an ablation. Compare fresh baseline
scores, not historical saved percentages. Suite output includes
`scratch_role_coverage`: eligible/declined case counts, guard-check counts and
counts of cases encountering each rejection reason. An eligible proposal is not a
match. A declined case is not proof that no equivalent source exists.

Fresh trials also collect source-free repair-prefix costs under the same compiler
identity and mismatch shape. After four timed/measured attempts, continuation
ordering considers improvements, ordinary exact verdicts, identical outputs and
elapsed trial time. Older untimed aggregates do not acquire invented durations;
unknown output stays unknown. Existing exploration and global search budgets stay
in place. Reading agent summaries does not run a compiler.

Replay trials expose `instruction_resolution`: target mismatch sites recovered,
introduced and remaining, with a target-stream hash. Candidate insertions are
anchored to the preceding target instruction; these are not C source locations.
Lists are bounded at 32 entries. After recovery, region ranking can focus the
densest surviving window; whole-function exploration remains interleaved.

## Record-layout cold-start regression

Keep source/header checkpoints and ROM exports private. Use the existing
`python -m decompnet.app.checkpoint_benchmark` command with `--project`,
`--function`, `--checkpoints` (SHA256-bound manifest), `--output`, `--trials 32`
and `--seconds 30`. Run main and treatment against the same immutable export
and manifest with fresh state. Include both the near-final score-75 scope
checkpoint and the earlier score-70 structural checkpoint; report each separately.
An exact near-final recovery is not proof of cold recovery from the earlier root.
Record ordinary linked exactness, compiler calls, generated/tested family coverage
and wall time, including proposal generation. Never copy the private fixture into Git.

`record_layout_sequence` uses resumable finite pages before broad near-match
search. It composes existing guarded tail/conversion/storage/loop/scope edits,
retaining original physical text. Source-bound compiler mappings rank eligible
storage/scope proposals; edited parents invalidate stale line associations rather
than applying them to shifted lines. The normal judge, shared negative cache and
existing stop/time/compile budget remain authoritative. No background AI calls,
compiler overrides or live-project writes are needed for the benchmark.
