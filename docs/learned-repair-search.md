# Learned repair search

## Intermediate sequence audits and loop follow-ups

Use `python -m decompnet.app.linked_repair_benchmark --project <copied-export> --function <name>
--header <context.h> --audit-path <private-path.json> --trials 8 --seconds 120 --output <report.json>`.
The manifest contains `stages`, each with `path` and `source_sha256`. Source changes invalidate it.
Reports distinguish missing literal proposals, first-page selection, ordinary admission and
measured equivalent outputs. Known sources are controls, not discovered repairs. Unmeasured
equivalent candidates remain unknown; compiler failures are not generation failures.

Loop follow-ups reserve at most six ordinary-budget proposals from three measured parents.
Backedge → counter staging → consumed integer-constant placement can be explored before numeric
improvement, with known unchanged frames, at most four extra instructions and a bounded score
detour. Unknown frames and unsupported invocation profiles stay ineligible. Pauses retain the queue;
the normal judge alone establishes matches. An inert-only queue can advance up to four saved
proposal pages, at eight-trial intervals, while retaining periodic retries of deferred families.

## Replay evidence and independent edit checks

Replay evaluators must return complete `diff.rows`, not `diff.render()` or a shortened listing.
Rendered/malformed rows are rejected; absent rows produce explicitly unavailable diagnostics,
not a misleading zero-instruction result. Baseline and candidate reports retain structural
counts so an earlier unresolved checkpoint can be compared with a near-match checkpoint.
The private frozen-case manifest supports multiple checkpoints of one function using distinct
case ids. Keep source/header/target/compiler hashes and equal budgets; report failed recoveries too.

Automatic post-match analysis uses its existing four-trial budget to undo captured token edits
one at a time from the complete successful source. The repair dialog and summary export show
which individual reversions preserved output, which changed output or failed compilation, and
which remain untested. Independent reversions do not prove a combined reversion works, or that
retained edits are semantically necessary. Compiler-output evidence is separate from the normal
linked/objdiff judge. Selected source is never changed by this analysis.

If the complete before and after sources already produce the same output, record that fact and
do not propose a source-only learned recipe. Context changes or coupled edits can explain an
apparent final fix. Manual replay can use `--independent-edits --trials 24` for a larger independent
check budget; `--minimize` retains its existing subset/deletion search. Automatic work remains bounded.

The app records short repair sequences when a connected chain of tested candidates ends in an
ordinary exact judgment. It stores family names, compiler/build profile, initial mismatch kind
and success count, not game source. Chains of two to four steps can rank applicable families
in future searches. Compiler installation identity is included for ROM profiles; imported
projects stay isolated by their own build scope.

Shared family ordering promotes learned first steps while retaining the original exploration
queue. Deep local search can follow the rest of a learned sequence from its actual tested
parent. It regenerates candidates and retargets families using that parent's own diff after
each improvement or retained detour. A losing trial's diff is not the saved winner's evidence.
Recipes never manufacture a verdict, skip verification, or make unsupported families available.
Random rewrites outside the tracked local chain do not get credited as recipe steps.

## Project sweep time

Minutes per function is a maximum. Project sweeps start with half that time as a soft allowance.
Each measured improvement extends the soft deadline by half the configured time from that
improvement, up to the original hard deadline. After at least 15 seconds, a window of 32 fresh
scored trials containing at most six distinct function outputs can rotate early when no
improvement intervenes. Improvements clear that window. Baselines, cached misses, no-edit
proposals and failed compiles do not train the repeated-output window.

A rotated function stays open. It is visited at most once in that sweep and is eligible on the
next requested sweep, without editing its source or resetting passes. No function is paused.
Time/compile caps and cancellation remain authoritative. Explicit single-function runs retain
their full requested time; adaptive rotation applies only to project sweeps.

## Agent evidence and regression alerts

Function briefs can include up to four remaining assembly-difference rows, current-input local
trial counts, learned starting families and two recent near-score alternative summaries.
These notes are bounded and tied to build inputs. Full diff and history remain available on
request. Displayed assembly and similarity scores are advisory, not exact verification.

A failed recheck of the source captured in a local last-exact record raises a notification and
marks that record with the latest failure time/score. Different trial source, stale compiler
results and imported historical records cannot raise this alarm. Repeated failures update the
record without flooding notifications; a new exact judgment clears it. Input changes are
explained by the existing verification comparison. If the tracked inputs agree, the alert
still reports the failure rather than guessing a cause. The alarm itself does not alter code,
flags or function status.

## Controlled check

An isolated KMC scheduling checkpoint recovered 945 → 155 → exact using call initialization
and masked-value reuse. The first integrated run used nine compiles including baseline;
resetting the checkpoint with the learned recipe and cleared miss cache used five. Both took
about eight seconds on this host. This is a retrospective regression control, not additional
game progress, a fresh-game benchmark, or evidence of measured agent-token savings.
