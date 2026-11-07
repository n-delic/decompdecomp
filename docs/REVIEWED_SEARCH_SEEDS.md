# Reviewed alternatives and compact search briefs

A saved best score is not always the best starting shape for another repair. A diagnostic may improve
most of the function while changing its interface or temporarily worsening its overall score. Keep
that evidence separate from the saved winner.

## Agent workflow

1. Read `get_search_brief(function)` for current-input allocation priorities, mapped C lines, a few
   source references and negative experiments. This reads evidence; it does not run a compiler.
2. Use `compiler_experiments` with `compiler="current"`, the current revision and an explicit patch.
   Retrieve a promising trial with `get_compiler_trial`. Historical diagnostics need rerunning after
   this feature is installed; they do not contain its input/caller review scope.
3. Call `review_search_seed(function, trial_id, review_note)` with a concrete reason to investigate
   that shape and the interface/caller checks performed. This records metadata, not source adoption.
   `enabled=false` disables a root. The investigation tool preset includes this action.
4. Run the ordinary single-function **Deep permuter**. Up to three reviewed roots receive at most
   12 compile slots and 30 seconds, further limited to a quarter of the remaining run time and the
   global compile/time allowance. The phase runs once before parallel paths. Actual allocation
   captures consume the same allowance. No model is called by this phase.
5. Inspect the returned `reviewed_seeds` metrics and diagnostic trial IDs. A changed or unrecognized
   signature stays diagnostic-only even when exact. Review declarations, observed return use and
   callers, then submit through the ordinary judge. A review note is not permission to change ABI.
   An unchanged signature can be adopted only after ordinary exact re-verification; diagnostic
   near misses never replace saved C. Exact diagnostics stop broad search for review.

Reviews expire when the target, relevant symbols, compiler/settings/installation, header/dependencies
or selected caller-source evidence changes. Caller references are conservative textual evidence,
not exhaustive proof about indirect calls. Compiler profiles are not changed by selecting a root.

## Allocation targeting

Installed native GCC phase captures can join register dispositions, weighted reference counts and
live-range lengths to source lines. For a closed register cycle, a short list of existing-operation
scope repairs goes ahead of broader lifetime variants. The brief shows advisory legacy priorities
and the extra weighted references needed to cross the next competing priority if lifetime stays
fixed. Local reservations, conflicts and conversions still matter. Never insert fake uses, opaque
identities, fixed registers or inline assembly merely to manipulate these counts. Physical source
line breaks remain preserved by the scope generator.

Ordinary repairs and briefs remain available in other modes. Reviewed diagnostic roots currently
require a local ROM project: imported builds need immutable per-file diagnostic source/context
snapshots, and room guests need host-authorized diagnostic storage. Missing or unsupported GCC
captures fall back to a small diverse repair shortlist, not speculative allocation claims.

Small-context API sessions omit repeated parameter prose when fixed inputs would crowd out tool
results. Tool names, main instructions and validation constraints remain; transcripts record the
actual schema sent. Larger contexts retain the full descriptions.

## Private checkpoint replay

`decompnet.app.reviewed_seed_benchmark` uses the real ROM linker judge in a disposable copied project.
Prepare a scratch home/database containing the original nonmatching saved source, copied ROM/layout,
context and symbols; put `.reviewed-search-replay` in that home. Every project root and database must
be inside it. Never point this harness at the running app, live save folder or a repository checkout.

```sh
python -m decompnet.app.reviewed_seed_benchmark \
  --home /absolute/disposable/home --database /absolute/disposable/home/replay.db \
  --project game --function function_name \
  --checkpoint /absolute/private/checkpoint.c --reviewed-source /absolute/private/reviewed.c \
  --review-note 'Observed caller discards the result; alternative interface remains diagnostic-only.' \
  --output /absolute/private/report.json
```

The report separates two setup judge calls, search candidate builds, allocation-diagnostic builds,
ordinary promotion builds, CPU-only model tokens (zero) and elapsed time. Full-rebuild verification
and duration remain `null` unless a rebuild callback is supplied and actually runs after ordinary
adoption. A signature-changing exact diagnostic receives no adopted match or rebuild credit.

### Measured regression, 7 November 2026

On a private N64 GCC 2.7.2 checkpoint (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`), saved score 215,
a reviewed scoped partial-return alternative scored 60. A cold replay found linked exact bytes with
one existing quotient scope in **4 search compiles** (2 phase-capture builds and 2 candidate builds),
plus **2 setup checks**, in **3.07 seconds**, using **0 model tokens**. It correctly stopped at the
interface-review boundary, left the original saved source unchanged and did not run a full rebuild.
This is rediscovery of a known repair with a supplied reviewed root, not evidence that the system
can independently infer that root or that a smaller model is already faster on unseen functions.
A control starting from the same score-215 source, with reviewed roots disabled and a 24-compile
allowance, made no improvement in 12.22 seconds. Both lanes are CPU-only; the reviewed lane is
given additional human-reviewed information. This is not an equal-information model comparison.
No game source, ROM bytes or database is included in this repository.
