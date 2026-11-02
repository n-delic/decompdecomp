# Replay captured repair examples

## In the app

Open **Agents → Repair examples** and use **Test automatic repairs**. It tests up to 20 examples
matching the category/review/project filters. Each expanded card also has a single-example action.
Choose 1–32 trials and 1–120 seconds per example; defaults are 12 trials / 30 seconds. Progress and
results appear in the dialog; **Stop replay** cancels compiler work through the shared job system.
Results are compiler-output regressions, not adopted game matches, and do not mark examples implemented.
Imported projects or incomplete historical context are reported as skips.

## Offline

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
