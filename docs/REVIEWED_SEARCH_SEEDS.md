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

The report separates setup judge calls (one in discovery mode, two with a supplied root), search candidate builds, allocation-diagnostic builds,
ordinary promotion and independent verification builds, CPU-only model tokens (zero) and elapsed time. Full-rebuild verification
and duration remain `null` unless a rebuild callback is supplied and actually runs after ordinary
adoption. A signature-changing exact diagnostic receives no adopted match or rebuild credit.

### Measured regression, 7 November 2026

On a private N64 GCC 2.7.2 checkpoint (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`), saved score 215,
a reviewed scoped partial-return alternative scored 60. A cold replay found linked exact bytes with
one existing quotient scope in **4 search compiles** (2 phase-capture builds and 2 candidate builds),
plus **2 setup checks**, in **3.10 seconds**, using **0 model tokens**. It correctly stopped at the
interface-review boundary, left the original saved source unchanged and did not run a full rebuild.
This is rediscovery of a known repair with a supplied reviewed root, not evidence that the system
can independently infer that root or that a smaller model is already faster on unseen functions.
A control starting from the same score-215 source, with reviewed roots disabled and a 24-compile
allowance, made no improvement in 12.22 seconds. Both lanes are CPU-only; the reviewed lane is
given additional human-reviewed information. This is not an equal-information model comparison.
No game source, ROM bytes or database is included in this repository.

## Automatic return-path discovery

When no reviewed seed is selected, local ROM deep search can now generate a small diagnostic
pair from the saved source and target alone. The MIPS reader follows reaching definitions of the
return register through conditional branches, branch-likely delay slots and traps. It distinguishes
a signed result-preserving exit from other exits that leave scratch results. Direct caller evidence
in `get_search_brief` distinguishes observed consumption, overwrite and unknown use; it does not
prove indirect-call closure or a defined return value on every path.

The initial C generator deliberately covers one narrow shape: a final braced signed 32-bit scalar
guard in a void function. It tests the partial-return hypothesis, then a separate lexical lifetime
for that existing tail scalar **even when the first edit scores worse**. Shared prototypes and
callers are not rewritten. Existing own prototypes, recursion, volatile/assembly and control
transfers in the proposed shape decline generation. Unknown processor readers also decline.

Generated roots use the same 12-slot/30-second/global-budget limits as reviewed roots and always
remain diagnostic-only, including exact results. Current-input failed scored diagnostics are reused;
compile failures remain retryable. Relevant quotient sites can include middle assignments: up to six
mapped sites, with depths 1–3 visited across sites before increasing depth. Estimated weighted-reference
crossings rank depths; the approximate +2 references per level is only a search heuristic, never
compiler proof. Captures and the ordinary linker still decide the measured outcome.

### Discovery replay, without a supplied root

Omit `--reviewed-source` and `--review-note` from the replay command above. This discovery mode bypasses
reviewed-root selection and reads neither a known matching candidate nor a previously reviewed seed
as search input. It uses the original saved C, frozen target/context/symbol inputs and the current
repair implementation. Restore those inputs in the disposable copy before comparing runs.

The private score-215 regression generated two roots itself and reached linked exact bytes in
**5 search compiles** (3 candidate checks and 2 allocation-capture builds). A separate original-baseline
check and independent final linker recheck bring the total to **7**, with **0 model tokens**.
Cold diagnostic-cache runs took **7.42, 3.64 and 7.19 seconds**; host/filesystem/toolchain caches can
change wall time. The saved source remained unchanged, the independent diagnostic recheck passed,
and full rebuild time remained unmeasured (`null`) because interface review was still required.
This is a known-case regression for a recipe designed from this investigation, not a held-out model
evaluation or evidence that arbitrary branching void functions should return integers.

The earlier supplied-seed replay and saved-only control above predate automatic discovery. The
benchmark now reports `preparation_seconds`, cached diagnostic hits, candidate/diagnostic builds,
independent verification builds/time, model tokens and full-rebuild time separately.

Planned gaps: analogous return-path readers for non-MIPS processors, broader source shapes with
immutable private prototype snapshots, imported-project diagnostic roots and room guest storage.
Ordinary search remains available in those modes.
