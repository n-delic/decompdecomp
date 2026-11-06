# Roadmap

- Done: saved-match recovery eligibility scans use the current database without
  triggering implicit progress-folder exports on every project/fork lookup. Explicit
  synchronization and frozen-input checks remain at each bounded recovery group.
  Empty candidate pools also avoid decoding headers.

- Done: saved-match recovery defaults to checking all available candidates, with Stop
  available throughout. Independent optional time and compile limits are collapsed by
  default, and older historical matches are no longer hidden by a global record cap.

- Done: saved-match recovery overlaps up to eight bounded compiler jobs, respects background
  worker and compile limits, skips forced synchronization for functions without candidates,
  and publishes each small group once. Fresh exact judgments and commits remain serial;
  cancellation discards pending results, releases group claims and preserves completed work.
  Candidate alternatives release the project lock between groups; throughput updates live.

- Done: app-only MCP startup probes validate authenticated project metadata without
  synchronizing saved files or waiting for their verification lane. Ordinary tools
  still synchronize progress; probes retain project-existence and authentication checks.

- Done: app and permuter SQLite writers share OS-backed admission before BEGIN, including
  schema initialization, match-library writes and maintenance. Waiting leaves readers and
  the local connection mutex free; optional bookkeeping stays nonblocking, cancellation and
  process-exit recovery release admission, and commit failures roll back without replay.

- Done: saved-progress metadata scans read function status once, import missing historical
  evidence in cancellable batches and hash shared headers once per scan. Recovery indexes
  historical donors by address while retaining target, relocation, ABI and exact-byte checks.

- Done: authentication reads token identity on every request but coalesces advisory
  presence timestamps to five seconds and skips them when a writer is busy.
  Presence may temporarily look offline under contention; revoked tokens stay invalid.

- Done: compiler-profile match application verifies up to eight candidates concurrently, reuses
  short-lived server-owned exact verdicts only for unchanged function inputs, and publishes
  settings/matches in one guarded transaction. Unrelated agent progress no longer invalidates
  every passing candidate; changed and ambiguous overlay entries are skipped. Exact matches release claims and redirect displaced workers.

- Done: dated usage-limit messages such as `resets Nov 9 at 5pm (Europe/London)`
  use the stated date and timezone, including near-year rollover. Invalid or distant
  dates retain conservative fallback waiting; no provider request bypasses a limit.

- Done: saved verdict metadata no longer restarts full-project verification; assembly decisions are scoped to their functions and overlay records retain distinct identities. Saved-match recovery freezes inputs after synchronization, ignores autosave-only metadata, waits cancellably for writers, and retains committed matches when derived indexing is busy.

- Done: SQLite writer acquisition releases the process-local mutex between short busy
  waits, retaining the configured timeout and never replaying transaction bodies.
  Other processes can still cause timeouts; this removes a local queue amplification.

- Done: a progress file disappearing during enumeration/stat/read restarts the entire
  read-only capture, up to three attempts. Partial captures are discarded; persistent
  failures, permissions, links, conflicts and ordinary exact verification remain guarded.

- Done: progress-file failures name rejected relative paths and bounded filesystem
  error codes/filenames, preserving link rejection and the original exception cause.
  This improves diagnosis; it does not resolve long synchronization lock waits.

- Done: shared saved-C baseline hits publish independent diff evidence for the requesting
  worker and lease, so `last_diff` can read a rechecked candidate without compiling again.
  Source/build scope guards and ordinary exact-match adoption remain unchanged.

- Done: copied `project/function` claim labels resolve with an explicit matching project,
  including imported `symbol@unit` paths and ordinary alias/held-claim selection.
  Exact stored names, project isolation and ambiguous-name errors retain precedence.

- Done: bounded independent integer/function-address setup permutations before visible
  loops and labels reach shared local, autopilot, deep/iterative, donor and replay paths
  for proven macro-free direct C32 GCC invocations.
  Dependencies, memory expressions, hidden types/preprocessing and volatile setup fail
  closed; no calls or control edges move and the destination exact judge is retained.
  Planned: source-preserving preprocessor span mapping is needed to cover included
  or macro-defined setup types. Imported/external builds and other compiler adapters
  also need complete invocation evidence before this rule can be enabled.

- Done: bounded terminal branch layouts reach shared local repairs, autopilot,
  deep/iterative search and repair replay. Candidate-owned directly called inline
  void helpers can be rewritten without editing shared headers. Unknown preprocessing,
  moved declarations and unsupported control flow remain excluded; every candidate
  still uses its destination compiler and ordinary exact judge.

- Done: attempt source and full repair evidence use checksum-verified compressed shared payloads,
  preserving IDs and lazy exact reads. Explicit cancellable backfill, opt-in daily failed-source
  retention and indexed 100-row compile-history pages keep bulk data off ordinary list reads.
  Existing atomic database compaction remains a separate idle-time action.

- Done: cross-game searches shortlist indexed fingerprints from open and closed games before
  live donor indexing, prioritize widely shared code and compatible compiler profiles, and try
  verified donors before unfinished candidates. Indexed transfers appear in saved results,
  with current-stage counts and visible rejection reasons; every transfer still uses the destination judge.

- Done: header cleanup waits cancellably for SQLite write-lock acquisition, with visible
  retry status and no transaction-body replay. Failed UI jobs expose a bounded recent
  application-log tail and their error details directly in an opened, copyable log panel.

- Done: deliberate user jobs start on independent foreground threads and bypass the automatic
  maintenance lane and its pacing. A cleanup click promotes an already queued automatic cleanup
  without duplicating it. Scheduled retention, resumed automatic recovery and repair handoffs
  retain background scheduling; database transaction/compaction guards remain intact.

- Done: deep searches retain the full user time allowance without early no-progress or
  repeated-output rotation. Historical rotated turns become pending for a full retry.
  User candidate limits, cancellation, matches and genuine errors still end a turn.

- Done: compiler-profile changes select affected identities before loading source/target rows and retain
  final race/byte guards. Boundary caches use project revisions and ignore room cursor churn; completed
  agent session history, cost estimates and efficiency summaries reuse revision-backed snapshots.
- Done: cross-game exact reuse loads and tries one donor project before visiting the next, including
  imported destinations. Imported donor scans fingerprint only matched-function units, report object
  progress and check cancellation/time budgets between objects; incremental discovery preserves prior leads.
- Done: ROM room deep searches continue from cached local inputs during host outages.
  Exact bundles and input-bound better attempts are queued durably and retried after reconnection;
  the host still verifies its own matches. Transport failures are retryable, not source-review failures.
  Planned: offline imported-room searches need a complete reproducible local build adapter;
  these drafts currently require the host build.

- Planned: MIPS `R_MIPS_LITERAL` pool linking with independently known target `$gp`,
  corresponding instruction/address witnesses and original data checks. The current
  ROM judge explains missing `$gp` or unsupported literal linking and keeps rejecting it.

- Done: explicit machine-draft regeneration generates fresh C and makes one local compile/judge
  attempt per function, without autopilot repair variants. A dedicated pool supports 1–64 user-selected
  threads (automatic up to eight), independently of shared background workers; room confirmation remains.
- Done: room clients reopen their saved project immediately while reconnecting, with a nonblocking
  update notice. Applied-snapshot availability survives restarts independently of live sync/presence;
  first joins and host approval/refusal retain their existing gates.
- Done: pending room submissions use indexed project-key bounds and SQL status filtering;
  the review drawer fetches metadata without loading completed evidence or private source/header bodies.
- Done: remaining room controls, repair selection, imported-project preflights, agent settings/tool
  metadata, upload disk I/O, static assets and status/log reads run outside the HTTP event loop.
  Read-only snapshots bypass saved-progress synchronization; mutations keep verification gates.
  Synthetic stalled-helper tests require health requests to remain responsive.
- Done: sharing-dialog reads run in the UI worker lane against the current database,
  so saved-progress verification cannot make them synchronize on the HTTP event loop.
  Network discovery, member/settings reads and response encoding stay off that loop.


- Done: Settings database cleanup with removed-agent history, a rolling age limit in days or weeks (with legacy UTC cutoffs retained),
  automatic enforcement, counted cancellable batches and preserved work/usage totals.

- Done: bounded named-donor materialization; indexed/revision-cached latest headers;
  bounded room match/attempt deltas with a race-safe cursor; layout-versioned audit caching.

- Done: removing an agent clears its app transcripts and API conversation while
  retaining saved work and usage totals; late callbacks cannot recreate cleared logs.

- Done: retain in-flight compile results during metadata-only progress imports;
  source, header, compiler and layout changes still invalidate stale results.

- Done: named autosave workers per ROM project, with saved Pause/Resume controls
  and thread CPU attribution in Background work. A shared slot bounds background
  sync concurrency; explicit saves and authoritative verification remain enabled.

- Done: saved, live maintenance CPU budget in Background work. Indexing, SDK naming
  and identical-function reuse share a cooperative percentage of one CPU core;
  foreground work and compiler subprocesses are outside this budget.

- Done: restore saved notes directly without per-note session/attempt history scans;
  retain original timestamps and measured score changes in ROM progress saves.

- Done: retry paused functions using attribution only, preserving running helper
  credentials on successful and failed scans without issuing new bearer tokens.

- Done: compiler-tested fresh drafts, bounded private MIPS callback/argument/frame recovery,
  owner-aware ROM data context, persistent generation/compiler/link diagnostics and cancellable
  draft regeneration without clearing matches or attempts. The default pass targets missing compiling C; an optional full refresh includes all unmatched functions. Room members compile locally with host leases and match confirmation. Imported m2c projects use their own build.
- Planned: callback/frame evidence readers for PowerPC, ARM and x86; explicit variadic, floating-point
  and complex/variable-frame ABI recovery. Ambiguous frames and unbounded data remain unresolved.


- Done: preserve overlay branch labels during ROM import, recover missing local labels
  at recorded instruction addresses in existing drafts, follow renamed entry symbols,
  and retry recovered failed drafts through the ordinary compiler and exact judge.
  Background draft planning now supplies the complete function inputs it uses.

- Done: allow flag context in reusable knowledge proposals while retaining explicit
  compiler-selection routing, function-note scoping and model/user review.

- Done: bounded matched-callee integer prototype candidates in shared ROM/project local repairs,
  with preserved explicit casts, unchanged shared headers and ordinary exact judging. Complex interfaces
  and argument-count changes remain review items.

- Done: distinguish original ROM container provenance from judging settings in new repair captures
  and full offline replays, with complete snapshot/hash guards and unchanged raw historical evidence.

- Done: unchanged saved-progress checks return under the project lock without waiting for
  unrelated shared verification work. Real imports, writes and judging retain the shared slot.

- Done: separate saved-progress project-lock and shared-verification wait timings in MCP
  transcripts, plus inclusive sync duration on return/failure, without changing synchronization.

- Done: MCP SQL connection, fallback-lock, execute and fetch timings on successful and failed
  tool calls; failed database-open/execute phases retain elapsed time without logging SQL or bindings.

- Done: numeric MCP transaction-writer and candidate lock acquisition timings,
  with synthetic rollback, release and claim-check regressions. This diagnoses
  contention without changing concurrency or exact matching.

- Done: keep newly generated native inline helpers private, with a real two-unit
  GNU89 link/execution regression and traced/untraced generator coverage. Extra
  helper code still requires complete project build verification.

Implemented: native expression expansion checks captured operand stability before
rewriting or deleting an initializer. ROM, imported-project and deep native jobs
share the generator guard. Unsupported control/memory/qualifier cases are skipped;
this does not prove equivalence of arbitrary native rewrites.

Implemented: the shared native permuter batch wrapper rejects a possible read before
initialization of an introduced ordinary scalar temporary using bounded CFG must
analysis. ROM, imported-project and deep native batches share the guard. Existing,
escaped, qualified or shadowed locals, constant controls and unsupported syntax receive no verdict;
this limited guard is not a general undefined-behavior proof. Generator-internal
walk reset feedback remains planned; every emitted descendant is checked independently.

Implemented: bounded constant field-store lifetime reuse inside a one-shot scope,
using an existing compatible consumed scalar and CFG liveness in shared source
repairs. Scope, escape, qualifier, macro and branch-entry guards preserve behavior;
manual semantic repairs remain separate and exact judging remains mandatory.

Implemented: bounded reversal of chained assignments to distinct, identically typed
ordinary struct fields in shared source repairs. Field layout, qualifier, macro,
array and RHS effect guards preserve conversions and one RHS evaluation; source
line breaks remain intact. Related SDK discovery and layout reconstruction remain
reviewed source hypotheses, with ordinary exact verification required.

Implemented: bounded integer-index to local-array pointer phase proposals in shared source
repairs, with explicit 32-bit metadata, unique array/scalar lifetime guards, and pure local
copy capture before a call. Macro expansions and argument-side writes remain opaque;
unrelated volatile accesses retain their source order and default CFG policy.

Implemented: bounded, sequenced byte-loop guard storage proposals in shared source repairs,
with MIPS target-evidenced stale counter-copy hypotheses and conservative local-use checks.
Guard storage proposals work in ROM and imported-source searches; counter-copy evidence adapters
for other processors remain planned.

Implemented: bounded source-owned byte-load macro address repairs in the shared deterministic
search, with explicit 32-bit ABI/type guards and normal exact-byte verification. Unknown
macro effects, conditional definitions and complex index expressions remain opaque.

Implemented: indexed legacy/scoped pause-record lookups for idle work receipts. Project-local point and
binary-range seeks replace the settings/function cross-scan while preserving the exact revision digest.

Implemented: batched candidate-scope inputs and revision-keyed, model-scoped paused-queue read snapshots.
Unchanged ROM queues reuse immutable scheduling results; source/header/symbol/callee changes and retry
records invalidate immediately, while imported external files are inspected on every decision.

Implemented: lightweight agent project facts and claim receipts, serialized claim retries, read-only live-claim
recovery, dedicated MCP health/metadata workers and bounded startup probes, numeric tool timing, connection
failure outcomes, current-profile compiler diagnostics with remembered rejected flags, and bounded assembly
fallback when mismatch mapping is unavailable. See docs/AGENT_WORKFLOW_EFFICIENCY.md.

Implemented: typed batch symbol/address ownership and unambiguous read aliases, cached full model-ID
resolution before worker creation, lease-bound release receipts for agent/room retries, and candidate
selection/writer/score provenance in stale and continuation responses. ROM and imported-project metadata
share the resolver; unavailable addresses remain unknown. See docs/AGENT_WORKFLOW_EFFICIENCY.md.

Implemented: guided mapped MIPS boundary evidence, neighbouring instruction windows, explicit before/after extents and one-shot agent recommendations with cited supporting/counter-evidence and uncertainty. Advice never edits layouts, claims work or compiles, and stale inputs are labelled.

Implemented: mapped MIPS boundary audit/quarantine, original-byte repair previews, retained-record delay-slot/join/end/padding/data/keep decisions and guarded undo. Guided evidence/agent investigation and repair adapters for other processors and imported object projects remain planned; source-free audit/repair evidence and limitations are documented in docs/CODE_COVERAGE_REVIEW.md.

Implemented: overlay-scoped data-region review with reversible new-claim pauses, explicit investigation
outcomes, independent-model verification requests, function-scoped prepared compiler evidence with a bounded
fresh byte recheck, and actual CLI tool handshakes before function handoff. See docs/AGENT_WORKFLOW_EFFICIENCY.md.

Implemented: room verification resolves compact automatic overlay names and can independently recompile an
entry from a small multi-function C submission, retaining declarations and line positions. Exact linked bytes/data
remain required; updated host builds invalidate failed-profile caches and pending explanations do not accumulate.
Function compiler profiles use original image offsets when overlays share RAM addresses; saves, exports, room
headers/results and the shared judging/search paths retain that identity.
Implemented: revision-scoped agent blocker review and retry explanations, validated structured
handoff experiments, bounded claim evidence, and immediate shared CLI usage-limit publication.
See docs/AGENT_WORKFLOW_EFFICIENCY.md.

Implemented: explicit reviewed MIPS raw-tail recovery with staged disassembly, original-prefix and overlap guards,
saved progress, and raw/export boundary reconciliation. Added a source-independent corruption benchmark
and bounded leaf review hints. New-function adoption and other-processor/imported-build extent recovery remain planned.

Implemented: source-free MIPS fallthrough/delay-slot review, legacy incomplete-entry visibility,
derived boundary-review filters and map state, raw/interior audit metrics, and a bounded
completion audit in the overview. Detected progress is never whole-image completion proof.
See docs/CODE_COVERAGE_REVIEW.md. Imported builds and non-MIPS processors still need
coverage/boundary adapters; compressed or unmapped programs require validated image mappings.

Implemented: transactional project progress rollups for ROM/imported projects; batched sidebar and
room claim reads; bounded overview/map/cross-project snapshots; changed-row map transfers; separate
lightweight UI capacity and a reserved foreground compiler slot. Simultaneous deterministic sandboxed
ROM compiles share only the compilation; linking, judging, attribution and persistence remain per request.
Private diagnostic/cancellation and custom filesystem compilation paths are not shared.

Implemented: bounded matched callback-owner discovery across selected/saved MIPS ROM source,
private-header declaration renaming during repository sync, and separately verified supplied
assembly through installed ROM compiler drivers. Source proofs retain provenance but are
reverified after restore. The overview separates four status counts and all-code C coverage.
Imported-build assembly verification and non-MIPS compiler-unit discovery remain planned.
Implemented: recorded-model exhaustion with worker-local fallback for unspecified models, plus explicit
bounded Retry paused functions grants. Same-model workers share pauses; different models retain their
own turns. Grants respect filters and held work, bypass only selected recent-release cooldowns and
are consumed in the claim transaction. Legacy unattributed pauses remain for audit, not as model
evidence. Room workers without recorded model metadata use the worker-local fallback.

Implemented: processor-owned MIPS local acyclic register-chain advice routes a bounded eight-proposal
lifetime/declaration lane in iterative and deep search without diagnostic compiles. Branch-target and
delay-slot residuals prioritize source control-flow repairs over literal edits. Closed-cycle budgets
and exact acceptance are unchanged. Readers for other processors and numeric register aliases remain
planned.

Implemented: temporary room HTTP failures (including gateway errors) preserve queued saved progress for retry
in ROM and project rooms, instead of treating them as the host rejecting the candidate.

Implemented: bounded residual follow-ups in iterative and deep search; typed later-local reuse for
subtraction, bitwise operations, unsigned literal shifts and scalar call results; dominated acyclic
branch regions and optional unambiguous GCC donor ranking. Shared source hypotheses retain the
normal judge and budget. Private frozen multi-case MIPS export comparisons pin all inputs; other
architectures/imported builds retain copied-evaluator replay. Wider shifts and cyclic live regions
remain unsupported.

Implemented: bounded single-call one-shot scopes, debug-aware initialization grouping and
repeated-float staging, and disjoint pointer/integer scratch unions. Closed register cycles
prioritize these lifetime hypotheses. One private KMC known-match replay recovered exact
bytes through three generated steps. Unknown imported per-file debug/ABI metadata keeps the
corresponding grouping/union trials disabled; build-owned metadata adapters and wider-pointer
union support remain planned. Call scopes and allocation advice use the shared source pipeline.
Implemented: explicit ROM function compiler suggestions freshly verify and atomically save
source/header/profile for one entry without neighbour confirmation. Disabled auto-apply waits
for review; legacy single-function range suggestions with saved C recheck on Apply. Broader
ranges and imported file/build settings retain their existing verification workflows.

Implemented: NOP-only MIPS targets remain rebuildable as set-aside assembly in ROM imports and project objects;
Check again applies the same classification to existing targets.
Incomplete nonzero targets still receive advisory boundary review; original bytes, symbols, matches and explicit
operator overrides are preserved.

Implemented: resumable local-ROM CPU source queues, explicit mapped/prepared assembly reconsideration
with atomic exact-only publication, dedicated interrupted-claim recovery, bounded real mapped
caller/callee and predecessor discovery for MIPS compiler-unit judges, and measured coherent SDK-pair
ranking. Up to sixteen explicit alternatives share the bounded pass budget. Queue status is not a
complete-image verdict; repository sync retains the full rebuild gate. Imported-build queues and
non-MIPS compiler-unit readers remain planned.

Implemented: bounded typed pointer/index plus one-shot-scope repairs in shared source search,
agent experiments and the existing permuter pre-pass. One copied KMC register-only case
recovered exactly; broader compiler/processor evidence and additional slot shapes remain planned.

Implemented: measured statement-line layout and candidate-only narrowing-cast hypotheses, compatible actual
extern recovery, consensus missing-callee aliases, and original lines for existing inline helper bodies.
Private mixed-entry/callee-clobber evidence is recorded and defers ordinary signature searches. Private
translation-unit ABI adapters, non-MIPS normalization readers and imported private-header adapters remain planned.

Implemented: ROM room match submissions retain source, successful compiler/ordered flags and private
header evidence. Hosts rejudge exact linked bytes before atomically accepting function-only settings,
and sync compiler ranges/private contexts to clients. Pending submissions show reasons and support
retry; upgraded clients recover saved local matches, and hosts can recover older source-only submissions
with bounded installed-profile searches. Imported projects retain their existing build/tree verification;
portable per-file profile/header adapters remain planned.
Implemented: private same-file static-helper discovery with original line directives, bounded source-boolean
configuration alternatives, include ABI correction, and consensus exact-neighbour callback hypotheses.
Explicit matched-ROM export repair tries ROM-proven scalar references and retained-pool alternatives, caches
misses and publishes only after linked-byte/data plus export-layout checks. Imported build-owned contexts and
export adapters remain planned; scalar-load symbol reading currently covers MIPS only.

Implemented: lightweight authenticated helper connection checks retry temporary transport failures with
capped backoff before any model call. Configuration/authentication failures retain phase, duration and
redacted diagnostics; old generic connection pauses recover on restart. Session completion is distinct
from helper stopping, and reusable knowledge proposals need not wait for consensus.

Implemented: shared native collapsible boxes for function compiler settings and cross-project comparisons.
Exact target-byte peers include matched and unmatched functions; absent exact peers, bounded
processor-aware instruction-pattern similarity supplies explicit naming leads. Names use the existing
rename checks; similarity never establishes matching C.

Implemented: bounded actual missing prototypes, target-backed commented calls, isolated word-field
order hypotheses, real data-symbol bindings and a narrow measured MIPS terminal clear/flag branch.
SDK private-ABI and register/frame-only blockers receive distinct diagnostics. Full translation-unit
compilation/clobber adapters, general missing-branch reconstruction and non-MIPS operand/tail readers
remain planned; imported builds retain their own headers and receive body-only compatible trials.

Implemented: bounded mapped-source CPU recovery, actual ABI/call declarations, verified record transfer,
measured record-gap and coherent constant/extent trials, MIPS table-supported label/arm hypotheses,
focused stack/declaration/helper follow-ups, and opt-in atomic promotion after a fresh linked-byte/data
recheck. Cached measurements resume before broad profile searches. Private context/profile publication
remains ROM-only: imported-build adapters and non-MIPS operand/table readers are planned; unknown
source types, arbitrary missing arm bodies and ambiguous debug lines are never inferred.

Implemented: compact claims prioritize verified mismatch regions while keeping complete candidates; last_diff
can reuse an exact scoped baseline without compiling. Unchanged-output advice starts earlier with recent
change history and respects exhausted/released claims. Compiler failures include bounded declaration/context
guidance, and stale edits include current C near the requested anchor without fuzzy replacements. Claude
partial-message usage is deduplicated and reconciled with final totals; interrupted coverage remains explicit.
Implemented: private original range-predicate helpers and bounded mask-signedness context trials in
mapped ROM preparation; focused two-load repair ranking; MIPS candidate-only epilogue boundary
warnings; concrete comparison/immediate revision leads; current-source/build-scoped unsuccessful
probe summaries. Shared diagnostics/ranking reach applicable imported builds. Private predicate/field
header context requires imported-build adapters; cross-architecture epilogue readers remain planned.

Implemented: session transcripts auto-refresh incrementally with a pause toggle, preserve expanded entries,
and follow the bottom only when already there. New judge-recorded improvements appear as score timeline
markers; historical attempts without saved improvement evidence are not inferred from agent prose.

Implemented: startup claim packets and get_candidate include bounded C-line mismatch evidence for the exact
candidate/build; compact compile feedback retains it. Uses cached baseline builds and unchanged compiler flags.
Missing/unsupported line tables remain explicitly unavailable; mappings are advisory associations, not causes.

Implemented: function notes can be removed, managed-agent session notes append to one handoff, and saved
score evidence shows the session’s score reduction. Legacy notes are grouped only with session evidence;
remote workers without local helper-session history retain separate notes and unavailable score attribution.

Implemented: copy a complete selected session (including unloaded messages) to the clipboard; export all recorded sessions and full events for one project agent as JSON to Downloads,
independent of transcript dialog pagination. Unrecorded CLI internals cannot be reconstructed.


Implemented: ROM function compiler results show current project assignment and matched-function counts for
each exact compiler/ordered-flags profile. Imported projects own their build settings; equivalent per-file
profile usage needs a build-system adapter and remains planned.

Implemented: reached reference include-tree macros in explicit source contexts, bounded initializer-helper
expansion in preparation and shared local repairs, advisory folded object-base and retail switch-table
case/group comparisons, and explicit bounded batch rechecking of historical exact bundles. Table evidence
is MIPS ROM-only; equivalent readers for other architectures and imported build-owned table data remain
planned. Typed-layout synthesis and missing-case-body synthesis are not implemented.

Implemented: original object storage preservation, advisory local-static scope and extern-load
diagnostics, narrow-prototype/paired-word MIPS call evidence, and resumable per-profile saved-draft
preflight. The prototype reader is MIPS-only; equivalent evidence for other architectures is planned.
Imported projects retain their own build/profile workflow.

Implemented: bounded same-project verified-neighbour context hypotheses, direct separate body/header
checks, and explicit fresh rechecks of saved exact profile bundles for ROM projects. Imported-project
context reuse/pair submission needs an adapter for its own build headers; planned. MIPS internal
temporary-argument caller evidence is advisory; equivalent readers for other architectures are planned.

Implemented: referenced enum/object declaration recovery, strict aligned MIPS relocation hypotheses, unique independently linked small-helper naming and bounded helper/profile retries across budget boundaries. Source call/branch diagnostics retain adjacent instructions and mapped statements. Relocation recovery for other processors/imported-build targets and imported private-header application remain planned; behavioral source changes are advisory only.

Implemented: source preparation advances cached private profile searches, retains scoped link failures,
and explains reference/saved-draft call differences plus explicit helper declaration conflicts.
SDK version identification and automatic retail-source patching are not implemented.

Implemented: explicitly selected definition-preserving source candidates, bounded numeric conditional-branch
selection with original line layout, advisory repeated-address-load diagnostics; instruction-guided MIPS runtime width/ISA/ABI hypotheses, SDK-signature profile priority,
proven interior-address source repairs and advisory specialized-call diagnostics; bounded compiler preflight
before long ROM searches and after saved deep-search improvements,
explicit profile-induced match results, opt-in mapped source before deep mutations, advisory type/retail checks,
side-effect-free comparison and unsigned product order hypotheses, input-scoped retries, existing-record
structure-return and SDK-style setter hypotheses, typed local SDK candidates by name, separate MIPS function/export
layout diagnostics, and compressed-cartridge repository identity checks. See [the live API workflow](docs/LIVE_API_WORKFLOW.md).
Planned: runtime/specialized-call evidence readers for other architectures, preflight integration with imported
projects' own file-compiler workflows, standalone-layout adapters for
non-MIPS object formats, and export normalization for compiler-emitted prefixes. Imported projects retain their
existing objdiff/whole-image gates; no generic preflight changes their build settings.

Deep search now chains bounded local repairs after random improvements. Register reuse hypotheses share
all normal draft/permuter paths. Fresh exact judgments repair score-zero/open bookkeeping; plain return
conflicts can recover a buildable ROM candidate with an honest baseline and retained history. A bounded numeric/structural source frontier and immediate reseeding now preserve useful alternatives.
Project sweeps now use progress-sensitive soft allowances within the requested hard cap; rotated
functions remain eligible next run. Explicit single-function searches keep their requested limit.

Bounded integer/call repairs now include evidence-led local widening, candidate-owned callee prototype widths,
integer lifetime splits/merges, call distribution into branches, terminal goto leaves and switch layout variants.
They share the ROM/imported-project judge and existing budgets; a better similarity score remains a draft.
Bounded scalar CFG liveness now supports masked-value reuse, extra lifetime merges and m2c draft hints.
Unknown syntax gives no advice. General C/C++ CFG analysis, fallthrough switch rewriting and shared-header
prototype inference remain future work.

Targeted address association and call initialization now share bounded local repair paths.
Input-scoped experiment costs reduce ineffective family priority while retaining exploration.
Last exact source/header/compiler evidence travels with saved progress; imported evidence never
accepts code. MIPS stack diagnostics separate saved slots from local operands. Equivalent readers
for non-MIPS architectures and imported-project GUI diagnostics remain planned. Experimental
bitfield frame hypotheses require explicit local-family selection.

Narrow call-argument donor staging is shared by local search. Iterative and one-function deep
search share output-scoped ineffective-family rotation and an earned two-level continuation
for measured complementary repairs, within their existing time/compile limits. Imported
ELF builds now read real function bytes/relocations before trial restoration. COFF uses conservative
section-level output for rotation only, never function-depth continuation. Unsupported object formats
and remote room trials without host output evidence retain ordinary ordering/depth; add genuine readers
or host evidence before enabling continuation there. Source-bound debug-line associations can split
independent call/operand repairs into bounded local hypotheses while retaining original candidates.
Compiler/profile-scoped productive sequence outcomes now guide alternating exploration passes.

Connected exact repair sequences now guide shared family ordering and deep local follow-ups.
Each candidate retains its own mismatch advice. Compact build-scoped evidence reaches agents;
failed rechecks of locally captured exact source raise deduplicated regression alerts.
See [learned repair search](docs/learned-repair-search.md). Durable cross-restart alternative
source frontiers and controlled live agent-token comparisons remain future work.


Bounded allocator combinations now include CFG-dominated pointer-call branches, mixed initializer
clusters and paired value-phase splits/reuse. Search reserves regional recovery slots under the
existing time/compile caps. A read-only exported-MIPS-ROM benchmark records linked/data-judge
results and compile/time costs; imported and other-processor adapters remain planned (copied
measurement callbacks are already supported by repair replay). Controlled replay recovery is
not new game progress and full recovery from every earlier checkpoint is not demonstrated.

What's planned for decompdecomp, roughly in order, and why. Every item keeps the rule that matters most: a match is
byte-identical after linking (ROM mode) or objdiff 100% (fork mode). Nothing here accepts code that only does the
same thing.

Every console gets every feature it can: one pipeline, ROM mode and project mode alike. A "—" in the table below is
a gap to close, each with its item here (CONTRIBUTING.md, "Every console gets every feature").

Want to work on one? Open an issue first, so two people don't build the same thing. CONTRIBUTING.md has the rest.

Public projects with a verified full source image can protect partial units with
[`verify_image`](docs/whole-image-verification.md): source changes must keep the complete output
identical, in addition to passing the function judge. This shared project-mode check works with any console.
Verified Splat imports also support assembly-only checkouts and maps at the checkout root;
references still require the complete original image hash, and assembly contributes no recovered C.
Imports and Check again can also generate references for version-matched assembly images declared by nested
Splat Makefiles, including extracted compressed programs with their own original SHA-1 files. Each code section
is linked at its original address and checked against that image before the combined objdiff configuration is
published. Coverage notes identify the images included; opaque binary sections are not counted as functions.
Nested C builds still need their own objdiff configuration or a single supported top-level Splat layout.

ROM-mode compiler comparisons save the complete ranked results per function. Any compiled row can be
applied after revalidation. Bulk comparisons skip completed tests while their source, context, target,
installed tools and compiler profiles are unchanged; interrupted functions are retried on the next run.
Project-mode compiler comparisons remain a gap.

Kimi Code 2.x helpers use the same MCP tools and session controls as other CLI helpers, with isolated
configuration and request token breakdowns. Live integration checked on macOS; Windows and Linux
launcher behavior is covered by portable tests but still needs physical host verification.

N64 additionally lists PM matching GCC 2.8.1, EGCS 1.1.2, reconstructed SN64
970404 and original SN64 builds 0004/0006. All five profiles have real compile/link checks on macOS, with explicit
license acceptance for the original packs. Physical Linux and Windows/WSL
checks remain outstanding. Imported
projects keep their own build scripts. See [compiler coverage](docs/sn64-compilers.md).

Additional Linux/WSL N64 comparisons now include original EGCS, GCC 4.4 and
SGI IDO 4.1/5.2/6.0, IDO 5.3 C++ and MIPSpro 7.4.4. GNU profiles have compile
and linked byte checks in an emulated Linux environment. All five SGI packs now
pass real C compile/link checks after license acceptance,
including early ECOFF conversion; the C++ pack also passes a class test. Physical
x86 Linux and WSL verification remain gaps. macOS
now has an optional shared local Linux runner, with real GNU compilation, engine
byte checks, export-style builds and cancellation verified on Apple Silicon.
Native original-toolchain Mac builds and automatic VM execution of arbitrary
imported-project builds remain gaps; see [Linux VM coverage](docs/linux-compiler-vm.md). Experimental SGI/GCC 4.4 profiles are
explicit selections, excluded from routine installed-compiler comparisons.

GCC 2.8.1 code generation can now be paired with original SN ASN64 2.56 through
an explicit compiler profile. Real macOS checks cover arithmetic, calls/data
relocations, floats, branches, and seven byte-exact functions from an isolated
retail-game sample. The new C-only profile uses the same compiler selection,
comparison, per-function settings and export paths. Physical Linux/WSL checks
remain outstanding. Shared instruction diffs expose encoding-only disagreements
hidden behind identical disassembly aliases without weakening the byte judge.

PS2 Sony EE-GCC 2.9 (991111), 2.96 and 3.2 now use the same optional macOS
Linux runner; SN Systems EE-GCC 2.95.2/2.95.3 use native Mac wibo. Installed SN packs
whose path contains spaces run from a private temporary copy; their old driver embeds
an unquoted self-relative include path, so symlinks alone cannot fix it. User-provided
kits remain in place and still need paths without whitespace for these old drivers;
an adapter that preserves in-place kit access remains planned. Original
compiler binaries and the linked byte judge are retained. Physical Intel Mac
verification remains planned; Apple Silicon checks use isolated homemade programs.
CodeWarrior PS2 2.3.3/3.0.1 also run through native Mac wibo after explicit
license acceptance. Both pass homemade-demo matching, C++ method and export checks
on Apple Silicon; physical Intel Mac and retail-game checks remain planned.

## What works where

Each feature and the platforms it covers today. A — is a gap: pick one up (the notes under the table say where to
start). Keep this table true when a feature ships or reaches another platform. Each column's code is its console's
folder, `src/decompnet/consoles/<id>/`; a new console adds a column (CONTRIBUTING.md, "Consoles").

| Feature | N64 | PS1 | PS2 | PSP | GameCube · Wii | Windows PC | DS | Public decomps |
| :-- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Typed later-local reuse and bounded residual follow-ups | ✓ | ✓ | ✓ 32-bit scalars | ✓ | ✓ 32-bit scalars | ✓ 32-bit scalars | ✓ | ✓ by target ABI |
| Frozen multi-case exported-ROM repair CLI | ✓ MIPS | ✓ MIPS | ✓ MIPS | ✓ MIPS | — copied evaluator | — copied evaluator | — copied evaluator | — copied evaluator |
| Local register-chain and jump/delay-slot search hints | ✓ | ✓ | ✓ | ✓ | — reader planned | — reader planned | — reader planned | ✓ MIPS; other readers planned |
| Per-model pauses and bounded fresh-turn retry | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓; room model metadata falls back to worker |
| Targeted scalar scheduling repairs and experiment costs | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Portable last-exact evidence | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ historical, own build |
| Stack slot layout diagnostics | ✓ | ✓ | ✓ | ✓ | — reader planned | — reader planned | — reader planned | — GUI adapter planned |
| Learned repair sequences, scoped brief evidence and sweep rotation | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ build-scoped |
| Last-exact failed-recheck alerts | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ local judgments only |
| Status | recommended | beta | beta | beta | beta | beta | beta | the project's own |
| Start from your own copy | ✓ | ✓ | ✓ | ✓ unencrypted or decrypted program and modules (ISO, CSO, CHD) | ✓ | ✓ | ✓ | fork by address |
| Judge | link · compare | link · compare | link · compare | link · compare | objdiff | objdiff | objdiff | objdiff |
| Machine drafts (m2c, Ghidra for x86) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ Ghidra | — | by architecture |
| Compile-tested draft refresh and saved diagnostics | ✓ | ✓ | ✓ | ✓ | ✓ m2c | ✓ Ghidra | — draft generator planned | ✓ supported draft generators |
| Initial callback, forwarded-argument and overlapping-frame recovery | ✓ bounded MIPS | ✓ bounded MIPS | ✓ bounded MIPS | ✓ bounded MIPS | — processor reader planned | — processor reader planned | — processor reader planned | ✓ MIPS C; other readers planned |
| Autopilot: free matches from drafts | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ exact drafts of tiny shapes; Ghidra's made compilable | — | — |
| Drafts follow the header, made in the background; `redraft` | ✓ | ✓ | ✓ | ✓ | — | ✓ Ghidra's | — | — |
| C++ methods drafted as `Class::method` | ✓ | ✓ | ✓ | ✓ | — | ✓ (RTTI, and classes from the vtables constructors store) | — | — |
| Twins: free matches from identical code | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Resumable source/profile queue and explicit assembly reconsideration | ✓ ROM | ✓ ROM | ✓ ROM | ✓ ROM | — owned-build adapter planned | — owned-build adapter planned | — owned-build adapter planned | — owned-build adapter planned |
| Supplied assembly code/data verification | ✓ ROM driver | ✓ ROM driver | ✓ ROM driver | ✓ ROM driver | — own build adapter planned | — own build adapter planned | — own build adapter planned | — own build adapter planned |
| Exact C / verified source assembly / other assembly / open counts | ✓ | ✓ | ✓ | ✓ | ✓ counts | ✓ counts | ✓ counts | ✓ counts |
| Local mapped source preparation | ✓ | ✓ | ✓ | ✓ | body only¹ | body only¹ | body only¹ | body only¹ |
| Verified neighbour context hypotheses and separate body/header bundles | ✓ ROM | ✓ ROM | ✓ ROM | ✓ ROM | — own build adapter planned | — own build adapter planned | — own build adapter planned | — own build adapter planned |
| Bounded flag preflight before long search | ✓ ROM | ✓ ROM | ✓ ROM | ✓ ROM | — own build | — own build | — own build | — own build |
| Existing-record structure-return / typed inline-setter hypotheses | ✓ | ✓ | ✓ | ✓ | ✓ C shapes | ✓ C shapes | ✓ C shapes | by supported C architecture |
| Separate function and export-layout checks | ✓ MIPS ROM | ✓ MIPS ROM | ✓ MIPS ROM | ✓ MIPS ROM | — own build | — own build | — own build | — own build |
| Permuter on near misses | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Hand-written assembly set aside | ✓ | ✓ | ✓ | ✓ | ✓ | Visual C++'s exception-handling stubs | BIOS call stubs, pieces of functions | MIPS, PowerPC, ARM's BIOS stubs and pieces of functions |
| Pieces of functions joined to their function | — | — | — | — | — | ✓ (`scratch.merge_pieces`) | ✓ (`recover.merge_pieces`) | — |
| Agents' verdict "no C makes this" (3 agents of 2 models: set aside, red in the map) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Check again (and before helpers start, once after each update) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Kernel stubs set aside and named | — | set aside (BIOS calls) | ✓ | set aside, named by library and NID | — | ✓ import thunks | — | — |
| Reviewed existing raw-tail recovery (local ROM) | ✓ | ✓ | ✓ (scalar decoder) | ✓ | — | — | — | — |
| Derived incomplete state / raw coverage / completion audit | ✓ | ✓ | ✓ (scalar decoder) | ✓ | — | — | — | — |
| Read-only gap/pointer/interior audit / incomplete-target warnings | ✓ | ✓ | ✓ (scalar decoder) | ✓ | — | — | — | — |
| Guarded jump-table / local callback-consumer audit (scalar MIPS ROM) | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| Saved code-audit decisions and evidence (local) | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| Called whole-gap recovery (conservative import rule) | ✓ | — | — | — | — | — | — | — |
| Matching-only ELF32 entry extraction for scattered pools (ROM export) | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| Guided boundary repair and advisory agent investigation (local mapped ROM) | ✓ | ✓ | ✓ (scalar decoder) | ✓ | — | — | — | — |
| Function-end reports (`report_boundary`) | ✓ | ✓ | ✓ | ✓ | — | ✓ (agents agree, the rebuild checks) | — | — |
| Compiler ranges and suggestions | ✓ | ✓ | ✓ | ✓ | from scratch: per source file (`try_compiler`, Settings); else the project's build | per source file (`try_compiler`, Settings) | per source file (`try_compiler`, Settings) | the project's build |
| Verified cleanup of rare compiler profiles | ✓ ROM mode | ✓ ROM mode | ✓ ROM mode | ✓ ROM mode | — | — | — | — |
| Flag search (`search_flags`) | ✓ | ✓ | ✓ | ✓ | the project's build | per source file (/Oy-, /O1, /Ob1) | the project's build | the project's build |
| Compiler map and the judge's survey | ✓ | ✓ | ✓ | ✓ | — | ✓ per source file, Rich header | — | — |
| Range checks: a range applies after its neighbours confirm it | ✓ | ✓ | ✓ | ✓ | — | ✓ over source files | — | — |
| C++ classes from the code (`get_class`) | ✓ | ✓ | ✓ | ✓ | — | ✓ (named by RTTI where the program has it) | — | — |
| Struct layouts from loads and stores (`guess_struct`) | ✓ | ✓ | ✓ | ✓ | — | ✓ | — | — |
| The game's own debug symbols: names, types, prototypes | — | — | ✓ symbol table, STABS, DWARF 1; the compiler of each object file | ✓ symbol table, DWARF 1 | — | ✓ PDB (Visual C++ 6.0's PDB 2.0, MSF 7.00), linker map, COFF symbols | — | — |
| Twins across your games | ✓ | ✓ | ✓ | ✓ | — | ✓ (between PC projects) | — | — |
| Automatic attempts from similar verified C | ✓ (ROM) | ✓ (ROM) | ✓ (ROM) | ✓ (ROM) | — | — | — | — |
| Bounded automatic reuse, tiny templates and local SDK C | ✓ (ROM) | ✓ (ROM) | ✓ (ROM) | ✓ (ROM) | — | — | — | — |
| Shared knowledge (two models agree) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Tidy each source file once it's polished (shared types in one header, names, dead declarations) | — | — | — | — | ✓ | ✓ | ✓ | ✓ |
| Polish matched code (still matching) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Real names for the game's symbols (`rename_symbol`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | dtk and ds-decomp projects |
| Save and restore (`progress/`, Open a saved project) | ✓ | ✓ | ✓ | ✓ | the project's git; best attempts saved | the project's git; best attempts saved | the project's git; best attempts saved | the project's git; best attempts saved |
| Saved README preserves notes, with completion bar and function map | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reimport: a new split, matched work kept (Settings) | ✓ | ✓ | ✓ | ✓ | — | ✓ (Ghidra again, the newest rules) | — | — |
| Rebuildable export | ✓ | ✓ | ✓ | ✓ | the project | the project (`ninja rebuild`) | the project | the project |
| Verified Git export sync (explicit Settings action) | build.json export | shared export adapter | shared export adapter | shared export adapter | source already in checkout | source already in checkout | source already in checkout | source already in checkout |
| Commit and push bound portable progress | ✓ | ✓ | ✓ | ✓ | — own checkout UI planned | — own checkout UI planned | — own checkout UI planned | — own checkout UI planned |
| Progress bars for foreground jobs | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Rooms: decompile together | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Git export sync stages additions and updates only files it owns, validates each C candidate and the full rebuilt image, then publishes commit-ready files. It preserves build scripts and existing hand-maintained source, lists skipped candidates, supports cancellation and detects concurrent checkout changes. The N64 adapter was exercised on a private racing-project copy; shared export and imported-mode behavior has synthetic Git/build coverage. Custom layouts, including the GBA exporter, need an adapter before they can use this action.

Linked-image exports: progress, maps, disassembly and Check again work through objdiff. Agent edits,
autopilot, permuting, polishing, joins/splits and compiler changes for these **read-only snapshot units: —**.
They require a project adapter that preserves whole-program compilation and promotion locks while rebuilding
individual drafts. Ordinary source-object units retain the table's existing behavior. The first exporter is MIPS;
other processors need their own linked-object writer.

Where to start on a gap:
- **Public project build prerequisite hints** run in `ForkProject.run` for public checkouts. The other columns
  describe starting a game from scratch and do not use public-project setup.
- **PowerPC (GameCube, Wii) and x86 (PC)**: class recovery and the compiler map work on a project's objects in fork
  mode, not on a ROM; the readers are the part to write (CodeWarrior's vtable pointer is often not at offset 0). x86
  has its reader (`arch/x86_fields.py`): PC classes and struct layouts come from it (`Console.classes`,
  `Console.structs`); a PowerPC reader would give GameCube and Wii the same through those parts.
- **Xbox** shares the Windows PC column's code (`consoles/xbox/` only makes the program); a gap there is usually a PC gap.
- **Windows PC**: autopilot's Ghidra drafts compile, but rarely match beyond the tiny shapes exact drafts cover:
  Ghidra drafts a delinked object without its callees' prototypes, so a method call loses `this` and a callee's
  arguments are guessed (a draft fed the project's headers, through a Ghidra script that applies them, is the next
  step). Only games built with Visual C++ 6.0 to 2010 can match so far: Newer
  Windows games, below, is the plan for 2012 to 2015 and 64-bit. A class's constructors are one real name
  (`CFoo::CFoo`), so of two at two addresses (a copy constructor) only one can be written as a constructor: names.py
  would need the mangled signature in "names". A game's own DLLs come along when its folder or .exe is given by
  path; a dropped .exe comes alone (a browser doesn't hand over the folder), a project made before can't take its
  DLLs in yet (Reimport analyses the programs a project has), a project restored or cloned asks only for the .exe (`ForkProject.needs` names
  one file: the DLLs go in `orig/` by hand, as its README says), and a game whose .exe can't start (Visual C++
  2017 and newer) can't start from its DLLs either.
- **Windows PC debug symbols** (`consoles/pc/debuginfo.py`): Ghidra's function boundaries stay Ghidra's, though a
  PDB gives each function's size; CodeView embedded in the program (NB09, NB11: Visual C++ 4-5 with /PDB:NONE) and
  Visual C++ 4's 16-bit type records aren't read; a class's methods aren't written into its struct (C++ calls a
  method only through a declaration in the class), and optimised code's register locals (2005's S_LOCAL ranges)
  aren't listed. A game's PDB has to be beside its program, or dropped with it, at import: a project made before
  can't take one in yet.
- **Function-end reports** cut MIPS functions (`engine._cut`) and, in project mode, a console's own
  (`Console.cut_function`: PC's functions.json). DS and GameCube projects need theirs.
- **Tidying a ROM project**: its C is the database's, one function at a time, with one shared header per segment
  (`edit_context`), so there's no file to tidy (`app/readable.py`, `_untidy`). The header is the place to start: its
  types and names, then the functions' C that uses them, each still matching.
- **Struct layouts** read MIPS and x86 loads and stores (`arch/mips_fields.py`, `arch/x86_fields.py`); another
  processor adds its reader as its `Arch.field_accesses`, and structs.py does the rest. Still open on MIPS: the PS2's
  fifth to eighth arguments (t0-t3), and a pointer into the middle of a struct handed to a callee (`&this->pos`). On
  x86: an element of a global array reached as `[index + address]` (every address the code names is a global of its
  own, `D_<address>`, so the array's fields past the first aren't the element's type), edx's __fastcall argument,
  and the sign of a field the code only or's and tests.
- **Twins across games** and **restoring a save** work on ROM projects (`app/twins.py`, `app/saves.py`). In project
  mode a project's matched C is its git repository; twins across games pair two projects' target objects
  (`shared_reuse.transfer`, PC so far: GameCube, Wii and DS projects of two games would work the same way but
  weren't tried); what's missing is opening a save's best attempts in a new copy of the project.

ROM projects now keep an indexed library of verified functions from the user's other local games, report every
cross-game transfer, suggest nearby code and names, and can index names and fingerprints from user-selected local
SDK object archives. Global SDK folders are scanned once for every project, with unchanged-file caching and
per-project automatic or explicit library selection. Imported project target objects supply detection evidence too.
SDK identities now have a separate badge in function lists. Automatic claims and small-function batches leave
identified SDK code until after game code, across ROM and imported projects; explicit claims and user stars
still take precedence. Identification uses recorded provenance or unambiguous full-size archive signatures,
without changing project settings or awarding matching C.
Removing a managed folder removes its indexed references, while manual imports are preserved. The archive's source is never bundled; a signature is a hint until compiled C passes the judge.

Similar-code autopilot attempts are bounded and use the unchanged compiler/link judge on MIPS ROM projects.
They learn unambiguous symbol correspondences, try local-permuter repairs and proven donor flags, and persist
trial results and a resume cursor. Compiler-flag hits are saved as evidence for review, not silently applied.
Aligned MIPS word-table offsets can also propose a candidate-local numeric ID macro correction when the
source explicitly declares a 32-bit table and the macro has one unambiguous index use. Relocated offsets,
redefinitions, other uses and uncertain element sizes are skipped; every proposal still passes the linked judge.
Near-miss repairs now use percentage eligibility and assembly-guided mutations, plus an installed full permuter.
Aligned high/low address evidence can correct known symbols and combine that with literal/explicit field-offset
edits. Literal address addends and typed array indices now use compiled stride evidence, including adjacent
symbolic-high/numeric-low address pairs. Non-adjacent load/store low halves now use conservative straight-line register tracking and full-address
reconstruction before proposing repairs, including full-address aliases in candidate-local declarations and uses. Register copies, numeric-only upper halves, and control-flow dataflow
remain planned. Dynamic addends, ambiguous mappings and named-field reconstruction
still need deeper analysis. Candidate-local padding repair now handles simple struct definitions in the
candidate itself, with bounded proposals from aligned load/store offsets. Shared-header layout reconstruction,
complex/nested types and field reordering remain planned.
Repair evidence now describes whether the selected pair changes C tokens, layout, comments/spacing,
no source text, or lacks a saved failed source. These labels preserve source-line boundaries and string
literals; they do not auto-dismiss or implement suggestions. Bounded source hypotheses also include
masked/narrow-store integer parameter widths, reusing an unused scalar local for a pointer's final integer
live range, and splitting/commuting selected addition shapes. All go through the ordinary ROM/imported judge.
Fixed-width typed-pointer additions also get a scaled integer-address alternative on supported 32-bit
processors. Calls, increments, complex indices and unknown/64-bit targets are excluded; exact verification
is still required. Definition lookup keeps neighbouring functions on the same source line out of these edits.
Repeated byte masks through an ordinary local also get a bounded linked width/cast/reuse hypothesis,
optionally collapsing an earlier copy-and-mask of the same value. This submits the related edits together
even when individual steps worsen the score; escaped, qualified or subsequently modified locals are skipped.
ROM autopilot machine drafts and quiet transfer trials now retain the actual compile-invocation settings
in repair evidence, including range/temporary overrides. Historical project snapshots remain explicitly
weaker evidence; imported-project per-file build flags are still not recorded by this ROM-only compile path.
Fresh machine-draft captures identify their raw, refined or stack-buffer generating variant, without
automatically approving review records. Simple signed-local additions can also be tested through an inline
helper on supported 32-bit targets; ordinary exact verification must reject any helper left as a real call.
Pointer loads can be staged through a previously unused ordinary void-pointer local, provided it is
overwritten before its next read. This keeps evaluation in place and uses the same ROM/imported judge.
Free template/library/address candidate captures identify their automatic strategy and origin; their review
state still requires an explicit decision.
Debug-enabled MIPS ROM builds also try a bounded coupled pointer-to-integer storage and sibling-scope
hypothesis. Both edits are submitted together, including cases where a separate intermediate worsens
the score. Imported builds currently omit this family because their per-file debug flags are not available
to the shared repair scheduler; forwarding verified per-file settings remains planned.
ROM autopilot has a lazy saved-context fallback after its ordinary draft variants fail: reachable local
prototypes/types and the target's signature can supplement or replace shared declarations. Function bodies,
macros and initializers are excluded, strict m2c parsing is required, and at most raw/refined alternatives
reach the normal exact judge. Existing successful drafts incur no extra generation. Candidate-local context
for imported projects' separate file-context drafting route remains planned.

Deterministic candidates also split field/address expressions into pointer temporaries, materialize small
arithmetic/comparison literals, and materialize both floating comparison loads in either load order without
inverting the comparison. Saved drafts and fresh m2c autopilot try explicitly bounded byte stack buffers,
including canonical indexed fill loops. Guarded induction alternatives translate simple typed-pointer
loops to indexed accesses and back, plus a narrow string-loop latch repair. All five families reach the
same ROM/imported judge and agent-local family tools; unsupported syntax is skipped. Families rotate within
the candidate cap. These are bounded hypotheses, not general C control-flow reconstruction or semantic proofs.

Bounded scheduling repairs include debug-flag-gated statement/else line joins, floating-expression
hoisting before stores, and scalar parameter assignments split through compatible locals. After an agent
releases a tried function, a separate machine pass tries at most six candidates with an eight-second
scheduling budget (an in-flight compiler may finish later), before another agent edits it. It uses the
ordinary ROM/project judge, leases, per-candidate cache and repair provenance; room clients defer to their host.
Automatic claims pause a candidate immediately after its no-improvement budget is exhausted, or after three
ordinary releases in the same state. Source, context, symbol, compiler
or repair-rule changes reopen it, and explicit selection remains available. Imported builds also observe
source, include headers and common build configuration files; custom generated dependencies outside those
paths still need richer per-file dependency records.

Saved near misses now have a persistent bounded, width-three repair search, disabled by default with a separate
configurable budget after normal autopilot, a persistent round-robin cursor, and cached failed compilations.
It retains improving/equal-score candidates and chains address, literal and targeted local repair families,
ranked by remaining mismatches. Identical compiler outputs retain at most two source shapes for expansion;
frontiers and output counts survive runs, and changed repair implementations invalidate the frontier. This uses
the same MIPS ROM feedback; object-mode and other-processor support await the context/readers described below.
Generated targeted proposals now have persistent parent/pool cursors beyond the old shortlist. Numeric
improvements earn bounded complementary follow-ups, explicit offset evidence prioritizes operand sites, and
a small input-keyed proposal cache avoids repeated generation within a visit. Frozen equal-budget suites
report successes, failures and regressions. The existing object-mode/other-processor orchestration gap remains.


Saved repair candidates retain donor provenance, including conservative exact-reconstruction recovery for
older saved transfers. Verified repair pairs feed regional symbol learning; new data/callee bindings require
two independent pairs. Mappings can repair other saved candidates and stale/ambiguous evidence is excluded.
These mappings share the MIPS ROM pipeline; object mode still needs its per-file context and relocation work.
Saved MIPS candidates now get direct constant repairs without first needing an address change. Fixed shifts,
explicit multiply/divide factors and bounded repeated-literal edits share the normal judge and search budget.
Signed expression reconstruction, implicit pointer/array strides, variable shifts and other processors' readers
remain planned; object mode still needs per-file source adaptation.
Candidate-local MIPS signatures can now repair ordinary register-argument shifts: missing incoming parameters
and forwarded leading arguments in small single-call wrappers. Both byte orders use the same reader/judge.
Floating/aggregate/variadic arguments, stack arguments, nonempty wrapper signatures, complex calls and other
processors' calling conventions remain planned. Imported object projects still need per-file prototype support.
Tiny MIPS templates and explicitly imported SDK C go through the same judge. Verified regional pairs can teach
explicit pointer-offset edits; reconstructing named struct layouts and shared types remains planned. New verified
matches trigger a bounded, configurable automatic cascade. Project-mode support for these additions is planned:
per-file compiler context, template readers and SDK source placement must be integrated with the object judge.
Project-mode similar-transfer support is planned: align target-object relocations and adapt file-local declarations before trying
similar C through objdiff. Other processors need an instruction reader for this search.

## Recently done

- Project opening uses an accurate compact summary on reserved UI capacity; expensive diagnostics, build preflight and room refreshes arrive after the page appears. Header version reads no longer fetch shared header text.
- Optional startup work is split into named per-task threads with project context, progress and cooperative Stop controls in Background work. Heavy tasks remain serialized; unchanged ROM library archives are skipped using persistent input stamps.

- Focused agents are recorded through metadata-only creation, skip whole-project draft/reuse preparation and ordering waits, and show live creation stages with elapsed time. Worker-side saved-file synchronization remains required before work.
- Background contention: autosave keeps one-second dirty checks with 15-second deep file checks; reuse and startup scans share one maintenance lane with cooperative UI yields and lower priority; SDK corpus indexes are shared across projects; persistent local connection failures back off to five minutes plus worker jitter.

- Check again and helper startup reconsider obsolete agent tooling verdicts when
  the effective compiler supports a target-backed live return-address C repair,
  including fixes shipped under the same package version. Valid assembly, user
  choices, and claims remain protected; bounded ordinary judging verifies matches.


- The notification panel renders cached events immediately, shares an in-flight history request, offers loading/error retry feedback, and reads bounded indexed history using reserved UI capacity.

- Project maps read compact per-function history dates independently of contributor rollups, reducing cold opening work while retaining identical recency semantics and revision invalidation.

- Paused retries check filtered candidates incrementally, stop at the requested allowance, release shared locks between candidates, and report checked/ready counts in a background job.

- Polishing agents skip matching-only preparation and waits on start, restart and resume; agent creation remains visible in the Agents panel when the launcher closes.

- **Your own compiler on every ROM console.** N64, PS1, PS2, PSP and GBA games can be built with your own copy of
  a compiler they use, a build no pack has (an EE-GCC, a CodeWarrior PS2, an SNC, a PSP GCC, a KMC GCC, an IDO, an
  agbcc): Setup, Compilers & tools, "Your own PS2 compiler" (one per console, in its group), given the folder it's
  in, laid out as the pack's is (`kits/owncompiler.py`). It runs exactly as the pack does (the pack's command, wibo,
  the PS1's maspsx and assembler) from your folder, which is never copied; its compiler id starts with the pack's, so each console's
  flags treat it as that compiler. Checked on the N64, PS1, PS2 and GBA demos (a copy of each one's pack imported,
  matched and refused wrong C) and, per pack, byte for byte against the pack's own object (`tests/test_kits.py`);
  PSP's packs weren't on the machine it was checked on. GameCube, Wii and DS: see "Your own compiler for
  GameCube, Wii and DS".

- **Visual C++ 2017 for Windows PC, through wine.** The `msvc2015` pack (proprietary, installed after the user
  accepts it, 220 MB): Visual C++ 2017's last toolset (14.16, cl 19.16.27027.1) and the Windows 10 SDK it was used
  with (10.0.17763), from ojdkbuild's copies of the Build Tools, pinned by sha256. The SDK's headers are a toolchain
  of their own (`winsdk-10.0.17763`) that the pack names, so a later pack that uses the same SDK shares one copy
  (each version's folder says where it is: `includes.txt`). An install made from a toolchain's earlier entry (other
  archives, or its files laid out another way) counts as not installed, and Compilers & tools offers to install
  it again from the archives already downloaded; installs made before installs recorded their entry are judged by
  the archives they record. Two compilers, chosen per file in
  Settings or tried by try_compiler: `MSVC/14.16` (32-bit) and `MSVC/14.16-x64` (64-bit, the 32-bit host's
  program database DLLs beside it, so it runs by itself). wibo can't run them (under a patched wibo a hello world
  exited 99 and `#include <windows.h>` failed without a word), so on Linux and macOS they run through wine, which
  the user installs; the pack says how before downloading (`wine.py`). The app keeps its own prefix beside its
  toolchains and a wine server for it between compiles (0.2 s a compile instead of 2.7 s; it leaves ten minutes after
  the last one, or when the app quits). A build finds wine when a 2017 file builds (the app's build steps say where:
  `Console.build_env`), so a project configured before wine or the pack was installed builds one as soon as they are,
  and no older configure.py is given an argument it doesn't know. The build passes every path as `Z:\...`, and
  wine's output goes through a file: the services the first wine program starts outlive it and, given the build's
  pipe, held the build open for as long as the server ran. The older packs stay on wibo.
- **PSP.** A UMD image (read in place, also as a CSO) or the game's program, with its title and product code from
  PARAM.SFO. A PRX is placed at 0x08804000 the way the PSP loads it, then split by splat like any MIPS program; its
  import stubs are set aside and get the system functions' names (`sceIoOpen`) from the open-source PSP SDK's NID
  list, 2,491 functions in 91 libraries (on a PSP game with debug symbols it names 268 of its 274 stubs, all as the
  game's own symbols do; a NID it doesn't know keeps `<library>_<NID>`). The disc's other PRX modules come with it,
  each a segment laid out after the main program (that game: 28 modules, 9,508 more functions to decompile, 36,002
  more stubs set aside), their functions matching with CodeWarrior 3.0.1 too. The game's own debug symbols come with
  it: the symbol table and
  CodeWarrior's DWARF 1 (source files, prototypes, types, also for CodeWarrior PS2 games). Diffs read the Allegrex's
  VFPU and its own instructions through rabbitizer. Compilers: CodeWarrior for PSP 3.0.1 (11 builds), the PSP SDK's
  GCC 3.3.3 (1.3.1 and 1.7.1) and SN's pspsnc (known by its code, which moves registers with `or`: the demo built with
  it imports, matches at -O2 -G8 and rebuilds byte for byte). On a PSP game with debug symbols: 2,465 functions, 2,458
  named, CodeWarrior 3.0.1 matching its functions byte for byte. Every row of the table was then tried on the PSP
  demo and that game: autopilot matched 251 of the game's 1,968 open functions from drafts and twins 19 more, and its
  export with those 270 in C rebuilds byte for byte, from a fresh clone too. What that turned up is fixed for every
  console: the permuter now reads autopilot's drafts, get_class reads GCC 3's and SNC's vtables, SNC's C++ names
  match, a save keeps what was set aside, the export keeps switch cases in their function and links branches into
  other functions. Small data is read from each program's code, not its gp value (every module's info has one): that
  game keeps none, and the compiler test's drafts of 60 of its functions match 23 with it off against 12. Its export
  with all 28 modules (47,975 functions, 491 in C) rebuilds byte for byte in two minutes, from a fresh clone in four:
  the assembly between C functions is assembled a run at a time (1,216 objects; one per function kept ld busy for
  over 40 minutes), and 8-byte data as words (GNU as moved a module's doubles).
- **Autopilot on Windows PC.** Autopilot didn't run on PC: its free matches come from drafts that compile, and
  Ghidra's didn't (undefined4, in_ECX, a method's `this` dropped). Now it tries, for each function, exact drafts of the
  tiny shapes Visual C++ makes from a line or two of C, read straight from the code (getters, setters, constants, a
  global object's constructor call, thunks, vtable stores, float static initializers: `consoles/pc/drafts.py`), then
  Ghidra's draft made into C++ that compiles in the project (ghidra.h for its types, `this` for a method, callees
  declared by their calling convention). Each is judged like an agent's attempt. On a Visual C++ 6.0 game of 7,090
  functions, 670 have an exact draft and 524 matched for free (517 exact, 7 from Ghidra's drafts). The same drafts
  are made in the background (`app/drafts.py`, a source file at a time), follow the project's headers (a class that
  declares the function, a global's type), and `redraft` remakes one with an agent's declarations.
- **Windows PC: the frame pointer, per source file.** About half of a Visual C++ 2010 game's functions keep an ebp
  frame (/Oy-), which no C matched under the project's /O2. The import reads each source file's functions and builds
  the files that keep frames with /Oy- (`config/<version>/file_flags.json`, `scratch.frame_flags`); projects made
  before get it when the app starts, but for files with matched code. `search_flags` tries a file's flags (/Oy-, /O1,
  /Ob1) and keeps what matches, as `try_compiler` keeps a compiler.
- **Windows PC games bring their own DLLs.** Only the .exe was read, but many games keep much of their code in DLLs
  of their own (#12: a 2010 racing game's .exe is small; an engine and its game logic are two DLLs). Given by path,
  a game's folder or its .exe brings the DLLs beside it (two folders down, not in a launcher's or tool's own folder):
  each is a program of the project, its source files in `src/auto/<its name>/`, its functions named from its
  export table (functions.json's "names"), built with the Visual C++ its own Rich header names (the compiler per
  source file), its calls into other programs through its import table. DLLs are relocatable and many prefer
  0x10000000: the first keeps its address, a later one that would overlap is moved by its base relocations, as
  Windows moves it (delink.py does the same at build time), so func_/D_<address> stay names no two programs share.
  `ninja rebuild` and the rebuild check put each DLL back together against that DLL, and the import's other steps
  (thunks by its own import table, its debug symbols, classes, Reimport) read each program. Left out, each with why in the
  import's log: the C runtime, Windows' own names (a wrapper's ddraw.dll), middleware, copy protection, ASI plugins,
  ones the import can't start, and ones built a year or more after the program (mods). `consoles/pc/scratch.py`
  (find_modules), `Console.program_in` (a game that is a folder).
- **A Windows PC project rebuilds the whole game.** A PC project compiled each function and compared it with the one
  cut out of the user's .exe, but nothing put the program back together: its export was "—". `ninja rebuild`
  (tools/rebuild.py, standard library only) writes the whole .exe again, every function at its address from what
  `src/` compiles to where that is the game's code once its relocations are filled in from the project's symbols
  (a constant of its own, a string or a float, where the game has the same bytes), else from the delinked function,
  and the rest (headers, data, imports, resources) from the user's copy, and compares it byte for byte. It's the
  project's rebuild check too, so "Rebuild the game and compare" and every check of a join or a new analysis test
  the whole program with the matched C in it. On a Visual C++ 6.0 game of 11,846 functions it comes out identical
  with its six matched functions from C (constructors, destructors and exception handling), in under a second, and
  from a fresh clone of the project. Not a linker: the program's data isn't delinked, and Visual C++'s LINK.EXE would
  have to place every object, library and resource as the original did (and 2005's and later stop in wibo 1.2.0),
  so the code goes back where the game has it; moving code is the modding-ready check below.
- **A Windows game's own debug symbols.** A PC game that shipped its program database (.pdb), its linker's map or
  COFF symbols imported as if it had none. The import reads them now (Visual C++ 6.0's PDB 2.0 and the MSF 7.00 of
  .NET 2002 on, read by decompdecomp itself, no new dependency), using a PDB only with the build whose CodeView
  record names it: its functions and data get the game's names (functions.json's "names", methods as
  `Class::name`), its object files become the project's source files, and from a PDB its types, globals and free
  functions go into include/debug_types.h and each function's brief gets its prototype, parameters and locals. The
  demo built with 6.0's /Zi and /DEBUG imports with all 19 names and its two files, and C written with the game's
  own names and types matches. A .pdb or .map dropped with the game goes with it. No agent time; nothing leaves
  your computer.
- **The right Visual C++ pack at the drop.** A game of 2005-2010 was offered the 6.0-7.1 pack, and its import then
  asked for 2005-2010's: the page reads the program's Rich header (or its linker's version) as the import does.
- **Windows PC: reimport, and twins in and across your games.** Settings' Reimport works on PC projects: the
  program is analysed by Ghidra again and read by the newest rules (`scratch.reanalyse_program`), every function
  going to the source file that held its code, new code to files of its own, each matched function keeping its
  extent whatever the new analysis says there, names and each file's compiler kept, and the whole kept only when
  the code still rebuilds byte for byte. Twins never worked on PC: the targets are COFF objects, which twins read
  as nothing. They're fingerprinted now (`coffobj.py`), with names as C writes them, and code matched in one PC
  project is tried on its identical functions in another, where both files are built by the same Visual C++. A
  Visual C++ 6.0 game has 523 groups of identical functions (8 bytes or more) in itself; it and another PC game of
  the user's (a 2002 game whose program names no compiler) share no function of 32 bytes or more, so across games
  is proven on two hand-made projects only.
- **Windows PC: the compiler map, the judge's survey and range checks, over source files.** A PC game mixes
  Visual C++ versions (a 2010 game's Rich header counts files of 2010 SP1, 6.0, 7.0 and 2008), and each source file
  can have its own, but nothing measured which. `compiler_map` now works where a project's files each have a compiler
  (`app/filecompilers.py`): what the program's Rich header says built it (`Console.program_compilers`), matches by
  compiler, and the survey, which builds each file with agents' C with every compiler the program names
  (`ForkEngine.file_scores`, the file keeping its own): a function that matches with another and not its file's
  confirms it. The map spreads confirmations over neighbouring files (compilermap.build, a file for a function),
  and a run becomes a suggestion; a suggested compiler for a stretch of files, an agent's or the map's, applies only
  after a second file confirms it, walking out from the first and stopping at a file whose matches need its own
  (`rangecheck.evaluate`); refuted on both sides it's a quirk, with a note; a file nobody can tell goes to an agent.
  The Settings answer shows the survey and checks. On the demo, the file holding a function matched with 6.0
  refutes 7.1, built for real, and keeps 6.0; a Visual C++ 6.0 game's header names five 6.0 builds, so its survey
  has nothing else to try. DS has the code path (its files each have a CodeWarrior) but no Rich header and wasn't
  run.
- **Windows PC: function-end reports.** Ghidra sometimes runs a function on into the data after its return, or
  into the next function. `report_boundary` works on PC projects: once three agents of two models give the same
  end (an address, or an offset into the function), the function ends there and the rest becomes a function of its
  own, open to agents when it reads as code and set aside with what agents said when it doesn't
  (`scratch.cut_function`). As a join is, it's kept only when the game's code still rebuilds byte for byte, never
  touches a matched function, and one that doesn't hold is remembered. Cutting off only the padding Visual C++ puts
  in the function's own section is refused. ROM projects still ask the user. API helpers have the tool now too.
- **Windows PC: import thunks named and set aside.** A call to a DLL's function declared without
  `__declspec(dllimport)` goes to a `jmp [__imp__Name]` the linker adds. Ghidra marks those as thunks and the
  import left them out, so their bytes became one function with the code before them, and calls to them read as
  calls into its middle (`_D_0062F7A6`). Each is a function of its own now, named after its import (`_RtlUnwind@16`
  where Ghidra knows the import's arguments), set aside with why, and its import's name is its real name, so a
  caller's brief and draft read `RtlUnwind(...)` and C that declares it either way pairs with it
  (`scratch.name_import_thunks`). Projects made before get it at start, never touching a run a matched function
  calls into. On a Visual C++ 6.0 game: 22 thunks (WinSock, DirectX, Video for Windows, KERNEL32), 21 of them cut
  out of two functions of an older project, whose code still rebuilds byte for byte.
- **Windows PC games of 2005 to 2012 build with their own Visual C++.** A game built with Visual C++ 2005 or later
  imported but built with 7.1, so nothing of it could match. The `msvc2005` pack (proprietary, installed after the
  user accepts it) has 2005, 2005 SP1, 2008, 2008 SP1, 2010 and 2010 SP1, from the decomp community's archives, and each
  runs through the pinned wibo (2010's with the redistributable's `msvcp100.dll` beside it). The import builds a game
  with the build its Rich header names (`RICH_BUILDS`), or the closest of its version and says so, and tells a game
  whose compiler isn't installed which pack to install. The demo builds with each (`build.py 10.0SP1`), imports with
  that build, and agents' C matches. Functions inlined everywhere are found one by one between the int3s these
  compilers pad functions with: the demo built with 2010 had 14 of its 19 functions found, the five in the gaps run
  together. On real games: a 2005 game without a Rich header (its linker says 8.0) builds with 2005 SP1, its 28,777
  functions rebuild byte for byte, and 8 of 9 small functions written by hand matched on the first try; a 2010 SP1
  game builds with 2010 SP1, 27,023 functions, 6,583 exception-handling stubs set aside, and 4 of 8 matched at once
  (the rest: two return types guessed wrong, a function compiled with frame pointers, which `#pragma optimize("y",
  off)` gives, and a C runtime function built for size). That game keeps an ebp frame in about half its functions:
  if all of it was built with /Oy-, a project flag would serve better than a pragma per function.
- **PC projects get the newest build tools.** A PC project kept the `tools/` it was made with, so a newer names.py
  (constructors and destructors paired by their class) or delink.py never reached one. The template's tools have a
  version (`scratch.tools_version`, the manifest's "tools"): a project with another gets them when the app starts
  (`WindowsPC.on_start`, `scratch.write_tools`), keeping its compiler and flags, in one commit and feed line, kept
  only when the rebuild check stays as it was and tried once per version; agents' sources are never touched. A demo
  project made by the previous release got them with its rebuild identical.
- **Windows PC: `split_file`, and a compiler per source file.** A PC project's source files are runs of about 4 KiB
  of functions, so one can span two of the game's files, or two compilers (a game's Rich header often counts objects
  of Visual C++ 6.0 and 7.1). `split_file` divides one (`WindowsPC.split_source`: a unit of functions.json), kept
  only when the rebuild check stays identical and every match holds; each file can have its own Visual C++
  (`config/<version>/file_compilers.json`, which tools/configure.py reads), switched by `try_compiler` and the
  Settings panel as on the DS. On the demo, a file whose matched function 7.1 compiles otherwise stays with 6.0.
- **Newer PC games get source files of many functions.** From Visual C++ 2005 on, the linker aligns every function
  to 16 bytes with int3, and the import ended a source file at any gap between functions: a 2005 game got 19,028
  source files for its 20,863 functions, and a first build of 20 minutes. A gap that is only the padding the program's
  linker puts before most functions no longer ends a file (`consoles/pc/scratch.py`, `linker_padding`): the 2005 game
  gets 1,286 files, a 2010 one 1,324 (its Rich header counts 855 compiled files), and a Visual C++ 6.0 game, whose
  compiler pads its own functions, keeps its 1,851; the rebuild check stays identical. Projects made before keep
  their files.
- **A PC game's import says what it is and whether it can match.** A newer game said only "expect little to match",
  and a 64-bit one was refused at the drop with "decompdecomp starts PC games from their 32-bit x86 .exe". The import
  reads the program's headers (`consoles/pc/scratch.py`, `describe`): the Visual C++ version and build that compiled
  the game's own code (the Rich header's compilers, the build its linker shares; without a Rich header, the linker's
  version), how many files were built with whole-program optimisation (/GL), 32 or 64-bit, and the DRM or packer
  its sections show (SteamStub, SecuROM, SafeDisc, StarForce, Themida, VMProtect, Enigma, Denuvo, UPX), only named,
  never removed. 64-bit programs, Visual C++ 2017 and newer (out of scope for now), mostly-/GL games and wrapped ones
  aren't started, each with what it means; 2005 to 2015 games import, told they can't match until their compilers
  come.
- **C++ classes on Windows PC, by their own names.** A program built with `/GR` keeps each polymorphic class's
  name (`.?AVClassName@@`) next to its vtable, through the RTTI Complete Object Locator. decompdecomp reads it:
  `get_class` lists the classes (templates and nested names written as C++), each with its vtable, virtual functions,
  base classes and the functions that store its vtable (constructors, destructors); `get_function` names the class
  of a virtual function, and Ghidra's draft of a method reads `Class::func_<address>(...)` with `this`. Methods
  written that way match, and a renamed one still does. A 2008-era game: 1,522 classes, 3,517 methods drafted as
  `Class::method`; a 2014 rebuild of a 2004 game: 557 classes, 2,940; a game of about 2000 built without `/GR`:
  only the 34 classes of its MFC library. Their fields, and the classes of code built without `/GR`: next item.
- **Struct layouts and class fields on Windows PC.** x86 has its reader of what each function does with pointers
  (`arch/x86_fields.py`): `this` in ecx, the arguments on the stack (through esp, followed across pushes, calls and
  `__chkstk`, or an ebp frame), the words loaded from them, globals. `guess_struct` and the brief's layout lines
  work on PC projects as on MIPS games (a struct through `this`, arg0-arg3, what a function returns, a global), and
  `get_class` shows each class's fields (offset, type, which functions use them), merged across its methods, with its
  bases' inherited. Classes of code built without RTTI come from the vtables its constructors store, named
  `Class_<vtable address>` until the vtable gets a real name. The demo's entity struct comes out field for field
  (bar one written only as an array element); a C runtime's FILE comes out as FILE is. A game of about 2000 built
  without `/GR`: 458 classes (424 from its code), 4,917 of its 11,696 functions with a layout for an argument; a 2001
  game without RTTI: 38 classes (every vtable its code stores), 2,544 of 4,793; a 2008-era game: 1,980 classes (458
  of code built without RTTI), 15,632 of 20,863; a 2014 rebuild of a 2004 game: 593 classes, 11,664 of 24,784, its
  entity classes' position, matrix and model index where the community's headers have them. Reading a game takes 5
  to 15 seconds, once per change to the program or its functions' extents (a rename reads only the names again).
- **Constructors and destructors on Windows PC.** A Visual C++ game's small exception-using functions are often
  constructors and destructors, and a class's two are at two addresses: named after an address, only one paired, and
  a destructor written so (`func_004D9DD0::~func_004D9DD0`) was refused outright. An agent now writes
  `CFoo::CFoo` for one function and `CFoo::~CFoo` for another; the attempt gives each that real name, and names.py
  pairs `??0CFoo@@...` and `??1CFoo@@...` with the original's symbols, calls from other functions included.
- **Windows PC: exception-handling stubs are out of agents' way.** C++ built with Visual C++'s `/GX` puts the code
  that destroys a function's local objects during an exception, and its handler, in `.text$x`, which the linker puts
  after all of the program's code. Ghidra takes each stub for a function, the smallest in the project, so agents were
  handed them first. The import now sets aside the stubs in the run that ends the code (`scratch.exception_stubs`),
  with why (functions.json's `generated`), and projects made before get it at start. On a 2000s Visual C++ 6.0
  game, 4,677 of 11,877 functions, every one in that run and none in the main body or the C runtime; on a 2000s game
  rebuilt with Visual C++ 2008 tools, where dynamic initializers follow the run, 6,581 of 24,843. Hand-written
  assembly on PC isn't recognised yet (x86 has no `asm_reason`).
- **Pieces of functions joined on Windows PC.** Ghidra starts some functions in the middle of others: a lone
  epilogue after a call it took for one that never returns (`throw`), a catch block or an `__except` filter after
  the function's return. Neither part could match. Code that reads or restores ebx, esi, edi or ebp before saving
  or setting it is joined to the function before it when that one runs on into it, set up the ebp frame it reads,
  or jumps into it with a short jump (`arch/x86.piece`, `consoles/pc/scratch.merge_pieces`): on a PC game of about
  2000, 181 of 11,877 functions. MSVC's exception-handling funclets at the end of the code read their parent's
  frame too, and the first follows a function with a frame of its own: they're set aside first, and no join takes one.
  PC projects have a rebuild check now: the program's code put back together from the delinked functions
  (`scratch.check_code`). It found two older analysis faults, fixed for new imports: functions found in gaps lost
  the zero bytes their last instruction ends with (`ret 8`), and a jump table running into the next one had its
  entries relocated twice. Older projects get what the import learnt when the app starts or on Check again, one
  step at a time (the manifest's `analysis`): the repairs, the stubs set aside, then the pieces joined, never a
  stub; each step that changes code is kept only when the code still rebuilds byte for byte (on that game: 23
  tables, 390 functions, 179 pieces). Agents' `join_functions` works on PC too.
- **Saved guides keep your notes.** Saving creates a basic README only when one is missing. Later saves update
  counts and visuals inside hidden comment pairs, preserving the rest of the guide. A local SVG progress card
  shows completion, platform and a function map in ROM and project mode; it contains no game code.
- **Real names on Windows PC.** A PC project names functions and data by address (`func_0041B470`, `D_0066DDA4`),
  and `tools/names.py` pairs them with the original's symbols by that address, so rename_symbol refused them. A real
  name is kept by address in `config/<version>/functions.json` ("names"), and names.py takes it as that address in C
  and C++ (a method's too): the function and its callers still match. The report keeps the original's symbol, so the
  function's row does too, and agents find and call it by its real name.
- **The compiler of each object file, from a PS2 game's symbols.** Games linked by CodeWarrior's linker are often
  GCC's code with CodeWarrior's C library: the three with symbols had GCC's mark on 91-93% of their object files.
  The one that built most of the code is the game's, and SN's GCC is told from Sony's by its own code; the others'
  object files get their ranges at import, and Check again suggests them to older projects through the range check.
- **Polishing reaches every function, then tidies each file.** A floor of 8 instructions and a ceiling of 400 kept
  polishing off a game's smallest and biggest functions for good: every size comes now, the middle first, and a
  function three tries didn't make readable is left. Once a source file's functions are all readable, a helper tidies
  it: types it shares with other files in one header, fields and classes named, the globals it still knows by
  address named (read_data first), dead declarations gone, one naming style (`tidy_done`).
- **Every compiler of the console, then the test of them all** (N64, PS1, PS2). The import offers to install the
  console's missing compilers, and a project page offers it until the first match; the compiler test then tries
  every one. A guess from the program alone can miss: one game's GCC code leaves its return delay slots empty as
  often as CodeWarrior's.
- **PS2 programs splat couldn't read** (#44): one without section headers, one whose DNAS block is marked as
  register info, one whose sections have no names, one with an empty section below its code. All import now.

- **A CodeWarrior version per source file on GameCube and Wii** (#26). A project started from scratch builds a
  source file with its own version when its `Object(...)` names one (`mw_version="GC/1.2.5"` in configure.py, which
  dtk-template reads): `try_compiler` switches a function's file, or tries them `"all"`, and Settings switches a
  file, a range or the whole game, keeping every file whose matches still hold. An agent found a file whose code
  matched only with GC/1.2.5 and GC/1.1 where the project built everything with GC/1.3.2; a file that mixes versions
  is split first (`split_file`), then the new file switches. Public decomp forks still build with their project's
  own compilers.
- **Visible progress while waiting.** Saved projects, room setup, exports, project checks, compiler changes and
  installations show a labelled work bar beside their logs. The bar clears on completion or failure; saved-project
  cancellation clears it too. Each action says when it starts and finishes, or why it stopped. Upload percentages
  still measure bytes transferred.
- **Names in project mode.** rename_symbol renames a function or a global in a project too: in the game's symbols
  (DS: ds-decomp's symbols.txt; GameCube, Wii: dtk's) and in every source and header file, kept only when the game
  still rebuilds byte for byte and every match holds. Polishing's names were local only before (func_0205d0f4,
  data_0222f194 stayed). Windows PC's names come from addresses (`names.py`): it says it can't rename yet.
- **Polishing on every console.** Helpers that make matched code readable (real names, types, a comment) worked on
  ROM projects only: a DS game at 57% had nothing to choose. In project mode a rewrite goes through the project's
  own file and is judged like any compile of a matched function: committed when it and the file's other matches
  still match, else the file is put back. Helpers there keep the function's name and describe types in the project's
  headers (edit_file).
- **Agents join what the rules miss** (project mode). `join_functions(function, with_function, reason)`: once 3
  agents of 2 models say two functions are one the analysis cut, the console joins them (DS: `recover.join_functions`,
  across two source files too), checked as an analysis is (the game rebuilt byte for byte, else taken back), never a
  matched function; a refused join is remembered. Check again only ever joins, so what agents joined stays. Other
  project consoles keep the agents' agreement until they can join.
- **What no C makes is out of agents' way, and progress counts the rest.** On a DS game 159 of 557 open functions
  were pieces of others (ds-decomp's symbols in the middle of functions: a lone epilogue, a register save running on
  into the next symbol, a lone `bx ip`), each given up on by five to seven agents in turn. ARM code that pops pc
  without having pushed, jumps through a register its caller set, uses r4-r11 before saving them or never returns
  is set aside (`arm.piece`), and a
  DS project joins such pieces to the function they belong to (`recover.merge_pieces`, checked by dsd): on that game
  119 joined, 4 functions matched at once with the C agents had written, no match lost, the ROM still identical.
  What shapes can't tell, agents can: `release_function(..., not_c=true)` with the reason, and once 3 agents of 2
  models agree the function is set aside, red in the map, until the user gives it back. Percentages and counts leave
  set-aside code out. **Check again** (About this project) looks at a project with what the running version knows;
  it also runs before helpers start, once after each update.
- **PlayStation (PS1) games from your own copy** (beta): a disc image (BIN, ISO or CHD; SYSTEM.CNF says which
  program it boots) or its PS-X EXE, with the disc's overlays. Functions are built as PSY-Q games were: GCC 2.6.3,
  2.7.2 or 2.8.1, then maspsx, which does what Sony's assembler ASPSX did, then GNU as; the import tries the three on
  the game's code. BIOS call stubs and unsupported GTE operations are set aside as assembly. Fixed rotation and translation matrix intrinsics let ordinary C callers pass the same byte-exact judge without accepting submitted inline assembly. The demo game matches function by function
  and rebuilds byte for byte; no real game is tested from start to end yet. A disc whose boot program is a loader
  gives the program it loads (`exe.loaded_program`), and where splat's guess at the code misses the entry point (a
  loader that is mostly a picture: 1 function found), .text is where the code's returns and frames are (137).
  Overlays inside an archive file (a game's areas, each with its code, one after another) are found too
  (`overlays.embedded`): on one PS1 game, 77 overlays in a 68 MB file, 26,483 functions besides the program's 2,398.
- **Nintendo DS games from your own copy again** (beta, #23): a ds-decomp project for the ROM, as in 1.0.2, now with
  all 24 CodeWarrior for DS versions and a version per source file (`config/<version>/arm9/file_compilers.json`, as
  DS decomps keep it). `try_compiler` switches a function's file to another version, or tries them `"all"`, and
  keeps the one it matches with when the file's other matches still hold: a game whose code mixes 1.2 and 2.0
  (1.2's `ldmia {..., lr}; bx lr` epilogue) no longer sticks at 80-95%. Machine drafts are the gap: a ds-decomp
  project keeps no assembly for m2c to read.
- **DS functions only pointers reach.** dsd's analysis follows calls, so callbacks, state tables and everything they
  call stayed outside any function (77% of one game's main program), and functions calling across those gaps never
  reached 100%. `consoles/nds/recover.py` finds them by their starts, adds their calls and pointers, and drops what
  dsd refuses to delink: 1,784 more functions in that game, every match kept. Projects made before it get them once
  when the app starts (`ForkEngine.reanalyse`): kept only when the rebuilt ROM is unchanged, and the project's
  activity says so. dsd also ended `.text` at the last function it found, and the 45 KB of code after it was
  `.rodata` to it, its 19 calls from `.text` unrelocated: `.text` now ends where that code does
  (`recover.extend_text`), with 207 more functions and 13 source files of their own. 2,987 functions in all.
- **A DS game rebuilt byte for byte.** The link stripped what nothing points at, and nothing points at an overlay
  holding only data (the game loads it by number): those came out empty, every overlay after them moved, and the
  rebuild differed in 209 overlays and every pointer to them. It links everything now (`-nodead`), and the header
  gets the copy's secure area checksum (of the area encrypted, which a dump no longer is; dsd left it 0). The game
  that differed rebuilds identical, its 1,784 recovered functions included. Projects made before get their build
  fixed at start (`scratch.write_tools`, kept unless an identical rebuild stops being one).
- **The permuter's real rewrites in forks.** decomp-permuter got no header in a fork, so it refused every
  function ("Syntax error in base.c": `u16` was no type it knew) and only the six simple rewrites ran, in every
  fork and on DS. It now reads what the function's file includes and declares.
- **Model routing.** Each model's share of its claims that matched, in the project, tells the models at work
  apart (on an N64 game free models matched 14-25% of what they claimed, stronger ones 42-77%). A model well behind
  the strongest one at work leaves functions given up on three times to it, and the strongest takes those first;
  near misses already go to the permuter. Nothing changes until two models have 20 claims each.
- **Helpers' effort.** The launcher's Effort, opencode's until now, is every helper's: Claude Code's (`--effort`,
  Low to Max with Extra high), Codex's (`model_reasoning_effort`, Minimal to Extra high) and the API helper's
  (OpenAI's `reasoning_effort`, Minimal to High, for servers whose models reason). A model at another effort is a
  helper of its own (`claude-opus-xhigh-1`), so its matches and spend are counted apart.

- **Agents try before they release, and say what a function waits for.** On a large x86 game agents released
  functions seconds after claiming them, untried (12,565 releases, median 5 s). release_function now answers a
  release before compile_and_diff's move-on rule once with what to do; a release without a compile doesn't count
  against the session; `needs=<function>` records a prerequisite: the function waits (claimed last), the one it
  needs goes first and is handed over, and the match brings the waiting one back first. Notes saying "already
  matched" on a function that doesn't match are marked out of date.
- **Rooms that say what's happening** (1.0.4-alpha). A guest waiting for the host sees "Waiting for the host to let
  you in" over the project, not an empty one and a red "Room offline"; then "Getting the host's work", counting down.
  A new guest starts at the room's newest event instead of replaying its history into the feed and notifications,
  and one that was away catches up a page at a time (the sync started from nothing at every start). Each computer
  has a colour on its agents' names, the same on every screen, and the Agents tab lists what the room's other
  computers' agents are on. A git project's join waits to be let in (with approval on, it failed), and a declined
  or removed guest is told so.
- **Room presence during slow syncs.** A separate heartbeat keeps members online while importing the host's work
  or rebuilding a source project. Sync failures and recovery are logged, and syncing continues automatically
  after a temporary connection failure. Applies to ROM and project rooms on every console.
- **Bring saved ROM progress into a room.** When joining with a game already imported, saved matches the host has
  not matched yet are submitted for the host to verify. Better unfinished attempts are shared when both projects
  use the same compiler settings; functions whose layout or name differs are skipped.
- **A standalone app.** Its own window (GTK and WebKitGTK) and a tray icon, so closing the window doesn't stop
  helper agents; **Quit** ends everything the app started. Only helpers the app starts work on projects: the old
  `decomp mcp` bridge is gone, and the first start removes its registrations from your agents. A development copy is
  a separate app (`~/Decomp-dev`) that never touches the installed one.
- **Helpers that restart by themselves**, with backoff; on a usage limit they wait until it resets. The API helper
  (any model with an API key) is the main helper: the app runs its whole loop.
- **One notification centre** (the bell) for every project, and **Report a problem** on every page.
- **Save and restore** (ROM projects): the work as plain files in `progress/`, every few minutes; **Open a saved
  project** rebuilds it on another computer from your own copy, and judges every function again.
- **Reimport a ROM project** (Settings): replace the split from a newly selected copy, keeping matched C only where
  its target is unchanged, plus notes, settings, saves and the names agents gave (functions are paired by address).
  A matched function the new split doesn't have keeps its C in a note. PC projects reimport too (Ghidra again);
  other project-mode consoles can't yet: their project's own analysis makes the functions, and Check again redoes
  what decompdecomp adds to it.
- **Polish matched code** (ROM projects): helpers give matched functions real names, types and a comment; a change
  counts only when the function still matches. The project shows matched and readable.
- **Shared knowledge**: what agents learn about a game reaches every agent once helpers of two different models
  agree on it.
- **Twins across your games**: code matched in one of your games is tried on the identical functions of the others
  (libraries, the compiler's runtime). No agent time; nothing leaves your computer.
- **A real permuter**: decomp-permuter rewrites near misses on the parse tree, weighted per compiler, on spare
  cores at a low priority. On nine near misses made from the demo ROM's functions it matched all nine, the old
  regex rewrites five. They stay as the fallback, for C decomp-permuter can't parse.
- **Better drafts**: m2c drafts every function in the background, from the header declarations its code uses, and
  again when they change; `redraft` runs m2c with an agent's own declarations; methods of known C++ classes are
  drafted as `Class::method`. On GTA San Andreas, 37 of 40 sampled drafts read the header (none did before).
- **Flag search** (`search_flags`, MIPS): each flag of the compiler changed once, up to 12 builds.
- **PS2 debug symbols** (#3): names and sizes from the symbol table; from STABS the source files as units,
  prototypes, parameters and locals in the brief, every type and global in the header; the same from the DWARF 1
  CodeWarrior writes (.debug, .line), with the line each function starts on. Big headers: the brief
  carries only the declarations the function uses.
- **PS2 kernel calls named**: the stubs (`addiu v1, N; syscall; jr ra`) get the kernel call's name: 154 in GTA San
  Andreas, 129 in Ridge Racer V.
- **Consoles as packages**: one folder per console (`consoles/<id>/`), a template and a conformance test. PS1 and
  GBA were public decomps only after the rewrite; PS1 has its package, and GBA's is back with its header check, its demo, its splitter,
  its judge and its export.
- **macOS app**: a native Cocoa/WKWebView window with the private window user agent,
  native open/save panels, downloads, Edit/Reload/Quit menus and the page's close confirmation. A uv source
  installer creates a Finder app in `~/Applications`. Intel and Apple Silicon CI exercise portable regressions
  and the hidden native window. KMC GCC/binutils require Rosetta on Apple Silicon; unavailable Linux-only
  compilers remain unavailable. Mac workers use per-thread QoS so they don't lower the whole app's priority.
  PS1 GCC 2.6.0, 2.6.3 and 2.8.0 build from pinned source on Intel and Apple Silicon Macs; GCC 2.7.2
  PSX/CDK and 2.8.1 use pinned native Apple Silicon builds with maspsx. The assembler helper uses the app Python.
- **More PS1 GCC variants**: GCC 2.5.7, 2.6.0, 2.7.2 CDK, 2.8.0, EGCS 2.91.66 and GCC 2.95.2 are listed for
  import surveys, compiler changes and export, each with a pinned toolchain and its own ASPSX profile. The demo
  can be built with each compiler and its game functions checked through the byte judge.
- **Linux packages**: a `.deb`, an `.rpm` and an AppImage with their own Python, built for each release.
- **Windows native app**: a WebView2 window, Start menu and desktop shortcuts, and a native tray icon.
  A custom native caption follows the app's theme, with Windows resizing, snapping and drag-to-restore. Scrollbars
  follow the page's theme too. Closing asks whether to keep helpers working. Kernel jobs end helper children after
  a session or an app crash. Tools have pinned Windows builds; Linux-only compilers use WSL 2 with Linux-side tool
  and build caches. Windows CI checks the native and portable code beside the existing Linux checks.
- **Struct layouts from loads and stores** (MIPS: PlayStation 2, N64). Every load and store through a function's
  pointer arguments, the pointers loaded from them and globals, made into types along the call graph: the brief
  names each pointer argument's likely fields (offset, width, how many functions agree), `guess_struct` writes the
  struct as C, says where else the type flows and which name matched code already gives it. Checked against the
  structs agents gave matched functions on GTA San Andreas: 95% of the offsets it proposes from other functions are
  right. PowerPC and x86 need their instruction readers (`Arch.field_accesses`).
- **Range checks** (MIPS ROM projects). An agent's "this range was built by another compiler" is a hypothesis: the
  judge tests the functions next to it (their matches, agents' attempts, the survey, habits), and where nothing
  settles it the nearest neighbour goes to an agent as a task. A range applies once a second function matches only
  with its compiler; refuted neighbours on both sides make it a quirk (dismissed, with a note on the function). The
  applied ranges are checked again the same way, by themselves. On GTA San Andreas's 72 ranges: 12 confirmed, about
  25 resting on one function, about 36 with no function whose C needs their compiler.

- **Compiler map** (MIPS: PlayStation 2, N64): which compiler built each part of the game. Measured on
  GTA San Andreas, compilers' habits only rule compilers out (-G0 code never reads through `$gp`; its CodeWarrior
  code never uses branch-likely), so what rules a compiler in is the judge: the survey builds agents' C for unmatched
  functions with each compiler the project uses, and C that matches with another compiler (and not today's)
  confirms it. Confirmations spread over their neighbours (one object file is one compiler) up to the furthest
  evidence; a run big enough becomes a suggestion, checked against every matched function. The ROM map shows it
  (Compiler view); agents read it with `compiler_map` and in `get_function`.
- **C++ classes from the game's code** (MIPS: PlayStation 2, N64). Constructors store their class's
  vtable into the object; from those stores decompdecomp finds each class, its virtual functions, its base class and
  every field its functions touch (offset, type). Agents read them with `get_class`; `get_function` names the fields
  of `this` a function uses. On GTA San Andreas: 507 classes, and the recovered entity layout (position at +4, matrix
  pointer at +0x14, model index at +0x22) is the one found by hand. Why: functions with virtual calls matched 7% of
  the time against 62% for the rest, because agents guessed the layouts.

## Next

### Your own compiler for GameCube, Wii and DS

Their projects (dtk-template, ds-decomp) take CodeWarrior by version from the community's compilers archive, which
holds every build known, so no game has needed one of its own yet. When one does: the project's build gets a
compilers folder of the user's beside the archive's (dtk-template's configure.py reads one), and the console lists
its versions (`Console.kit_compilers`), as Windows PC does for Visual C++ through `--own`.

### PlayStation 3 from your own copy

ROM mode, as the PS2 and PSP: you give decompdecomp the game's program decrypted (EBOOT.ELF: a SELF is refused, it
never decrypts anything), it splits it (the module descriptor's functions first, then calls and prologues), and the
judge links each candidate at its address and compares what each instruction refers to (`arch/ppc64.py`: TOC slots,
function descriptors, the call-site TOC restore). Its imports are named from their NIDs and its kernel calls from
their numbers (PSL1GHT's lists, MIT), and their stubs set aside. It builds with the free ps3dev GCC (the demo) or your
own SDK's compiler (the `ps3sdk` kit: the game's `.comment` stamp picks the copy). Every feature of the table ran on
its demo (`projects/demo-ps3`), and a decrypted retail game (22,482 functions, SDK 2.5.0) imported whole: its module
descriptor (Sony's 8-byte pairs), its import stubs (all 194 named), its nameless sections and its SDK read as they
are, and its functions matched through the judge with Sony's GCC rebuilt from its GPL source (SDK 2.5.0, the
game's own), a getter of a global among them: that game reached its globals through GCC's minimal TOC (r30), which
the import reads off the code and builds with (`-mminimal-toc`), and its linker didn't narrow any TOC load. What's left:

- **The period compiler without an SDK.** `packaging/ps3-gcc/` rebuilds Sony's `ppu-lv2-gcc` (SDK 1.9.0, 2.5.0, 2.7.0) from
  the GPL parts of its published source; its `toolchains.toml` entries wait for it to be published.
- **Drafts.** m2c has no PPC64 target: a ROM-mode Ghidra draft (as PC's) would give PS3 drafts, autopilot's draft
  phase and `redraft`.
- **The processor's readers.** `arch/ppc64.py` has no field accesses (`guess_struct`), class reader (`get_class`),
  piece rule or compiler map yet; the unstripped program's DWARF types and prototypes aren't read either.
- **Later:** the rebuildable export, PRX modules, SPU programs.

### Xbox: what's next

The original Xbox is a beta PROJECT-mode console: decompdecomp reads your default.xbe (or an .xiso / Redump .iso),
makes it a Windows program at the same addresses, and from there it is the PC path (Ghidra, COFF objects, objdiff,
`ninja rebuild`, which writes your default.xbe back byte for byte). With your own XDK set up in Setup (a kit:
`kits/`), its library code in .text is set aside and a game of an early XDK builds with that XDK's own Visual C++.
Three retail games imported and rebuilt byte for byte (thousands of functions each). All three were built with
link-time code generation (/GL, /LTCG), as most Xbox games were: the import tells it from the program (or the import
dialog says), units build in harness mode (`tools/ltcg.py`: the unit, a driver calling its functions as the game's
call sites do, and stand-ins, linked /LTCG by the compiler's own linker through wine), briefs show each function's
convention, `standins_for` writes a starting harness, and a register-convention function of a retail game matched
through its driver.
Briefs also say when a function is a member function (its `this` in any register) and of which struct, and an
attempt whose function got another convention than the original's says which registers differ. What's next is real
games: a whole one rebuilt from C, then the console's status can say recommended.

### Game Boy Advance from your own copy

ROM mode, as 1.0.2 had it: luvdis (MIT) finds the functions, agbcc (pret's GCC 2.95 rebuild) compiles the C, the
ARM/Thumb judge links each candidate at its address and compares the bytes, and the export rebuilds the cartridge.
The console, its header check, its demo, the luvdis splitter, the ARM/Thumb judge, editing and the export are back
(`consoles/gba/`, `projects/demo-gba`): the demo imports, all 123 functions are found after splitting/adding the
merged ones, 107 of the 114 with C match through the engine, and the export rebuilds it byte for byte with 71
functions as C. What's left:

- **Compilers and ranges.** agbcc's release also has `old_agbcc` and `agbcc_arm`, but the console lists only `agbcc`:
  selecting them per file and the compiler map/range checks/suggestions (`try_compiler`, `search_flags`) are MIPS
  only. GBA games built with another of the three need them.
- **Free matches.** ARM/Thumb look-alikes and twins, and the permuter's GBA weights (`agbcc` now maps to GCC's
  weights, but it wasn't run here); twins across games need the per-function Thumb mode too.
- **Code readers.** C++ classes (`get_class`), struct layouts (`guess_struct`, no `Arch.field_accesses` for ARM) and
  the game's own debug symbols aren't GBA readers yet.
- **Pieces.** The analyzer finds pieces (`arch.arm.piece`), but ROM projects have no join tool; `report_boundary` and
  saved-layout joining work at 2-byte boundaries.
- **Tidy** a source file is project mode only; a GBA ROM project's C is the database's.

Only agbcc's free tools are needed; the BIOS, Nintendo's SDK and the cartridge logo never are (the demo's logo is
blank).

### Newer Windows games (Visual C++ 2012 to 2015)

PC projects build with Visual C++ 6.0 to 2010 (phases B and C are done: Recently done). The import already tells a
newer game apart (its build, /GL, 64-bit, DRM); what's left, one phase at a time. Visual C++ 2017's compilers are
installed (`msvc2015`, Recently done) and can be chosen per file. A game built with a Visual C++ no pack has (2012,
2013, 2015, 2019, 2022, or another build of a packed version) builds with your own copy of it: the `visualcpp` kit
(`kits/`), set up in Setup from your Visual Studio's VC folder, matched to the game's Rich header by build (2015 and
newer through wine). Without a copy, games built with 2017 and newer are still refused at import.

- **What 2005-2010 still lacks.** The newer exception-handling tables (`__CxxFrameHandler3`) need the stub rules
  tested again (a game rebuilt with 2010 tools: 6,583 stubs found; a 2005 game built without C++ exceptions: none).
  2005's RTM isn't in the pack: its Rich header build is SP1's (50727), so SP1 builds both, and a game of 2005-2006
  may need RTM (decomp.me's `msvc8.0`). wibo 1.2.0 doesn't run these compilers' linkers (`lstrcmpiW`, added to wibo
  after it), which only the demo's build needs. On a Windows computer the pack is untested: there the compilers need
  their C runtime (msvcr80, msvcr90 through side-by-side manifests, msvcr100), which wibo has built in.
- **D, 2012-2013, then 2015.** The same, per generation: SSE2 float code by default from 2012, and one toolset for
  2015's updates.
- **E, whole-program optimisation.** With /GL the linker generates the code for the whole program, so one file's
  object says nothing about the program's bytes. Find out whether some functions can be judged by compiling their
  file with /GL and linking it with /LTCG against stubs; until then a mostly-/GL game isn't started.
- **Games built with Visual C++ 2017.** Lift the import's block (`scratch.LAST_IN_SCOPE`), map 2017's Rich builds
  to the pack (`RICH_BUILDS`: 14.1 build 27027 is `14.16`; the drop's `pcPack` and `_newest_mapped` follow), 32-bit
  games first, with a demo built by 2017 (its linker under wine). Until 64-bit games come (F), try_compiler's "all"
  and Settings offer the 64-bit compiler to 32-bit games too, where it can't match: a compiler's target would let
  them skip it. Under wine, /Zi with /FS fails (C1902: no mspdbsrv), which no project's build uses; on Windows the
  pack is untested.
- **F, 64-bit.** Read PE32+ programs and delink their relocations (nearly all relative), with the x64 calling
  convention. A 64-bit program lists every function's start and end in its exception directory (`.pdata`), so
  functions come from the program, not Ghidra's guess. Done when a 64-bit demo, then a real game, imports and
  matches.

### PSP: encrypted programs, ZSO images, imported names
Most retail PSP programs (EBOOT.BIN) are encrypted, and decrypting them takes Sony's keys: decompdecomp doesn't
carry them, so the user imports a decrypted copy (PPSSPP can save one). A pinned, checksummed decrypter installed
from Setup, like a compiler, would let a UMD image import as it is, and its encrypted modules with it (left out
now). Also: a module's own $gp isn't the judge's (one per project: a module that keeps small data where the main
program doesn't gets its own flags, but its $gp-relative data is named from the main program's $gp), its imported
variables (.rodata.sceVstub) aren't named, ZSO images (LZ4) aren't read, SN's pspsnc is tested on the demo only (no SN-built game was at hand: the
readable one is CodeWarrior's, the others encrypted), and the 2.4.1 CodeWarrior some games were built with isn't
archived (3.0.1 matches small functions;
bigger ones unverified).

### PSP drafts: the Allegrex's own instructions
m2c has no Allegrex target: the EE's reads PSP code, and knows `seb`, `seh` and `madd` but not `min`, `max`, `ext` or
`ins`, which PSP GCC emits from plain C. Drafts have `M2C_ERROR(unknown instruction)` there, and those functions
rarely match from autopilot (the guide tells agents what each one is). Rewriting them for m2c before it reads the
code (`min` as `slt` and `movn`, outside delay slots) would give those drafts their arithmetic back.

### Windows distribution

The Windows release MSI bundles Python and the app, creates Start and desktop shortcuts, and updates
through the app. Next: sign the installer and check for WebView2 and WSL during setup.

### Better splits reach existing ROM projects

A ROM project (N64, PS2) is split once, when it's made: a better split (code splat misses, function ends, compiler
ranges) only reaches games added after it. DS projects get theirs at start (`NDS.ANALYSIS`,
`ForkEngine.reanalyse`): versioned, kept only when the rebuild is unchanged, and the project's activity says so.
ROM mode needs the same, with more to carry: a split version in each project, and on start a new split whose
functions take over the old ones' work (matches, attempts, notes, claims) by address, kept only when every matched
function still matches and the export still rebuilds the game byte for byte. Functions the new split cuts or joins
go back to the queue with their old attempts as notes. Each improvement to the split bumps the version.

### How much usage each agent has left

A window that shows, for each agent you use, what's left of its limits: the 5-hour and weekly limits of Claude Code
and Codex, and a monthly limit or the credit left on an API key where the provider says it. Helpers already wait for a
usage limit to reset; this shows it before they hit it, so you can pick which agents to start.

### Big functions for every model

In a DS game at 58%, functions under 400 instructions were mostly matched, and 25 of the 29 over 400 were open:
40% of the code left, 10 of them never compiled. Agents of free models released them as "too big to transcribe in
one go", and they're right about the medium: a big function's Ghidra draft is 10-25 KB of C, more than one
compile_and_diff a model with a small output limit can send, and its assembly takes most of a small context.

The big ones that matched show the way. A 2,793-instruction switch matched in 2 compiles as 125 lines of C, and a
1,272-instruction one by a free model, in 2 compiles too: their cases are long runs of the same calls with other
numbers, and the agent wrote one-letter macros for them (`#define A(x) func_0206d4f8(x, 1)`). Two of the untried big
functions are in that file, macros defined, and their briefs don't show the match: a matched look-alike's C is shown
only under 3,000 characters.

1. **Briefs for big functions** (no new tool). Over 400 instructions, the brief says how such functions matched
   (macros for the runs of calls that repeat, the file's own macros listed), and shows a matched look-alike's C
   whatever its length, with its macros.
2. **One case at a time.** A `compile_part(function, part, code)` tool: the function's current C stays in its file,
   one part (a switch case, from the target's jump table: ARM's `add pc, pc, r0, lsl #2` and the branches after it;
   or a block between labels) is replaced, the unit is built and judged as always, and the answer is that part's
   rows of the diff. A first compile sends the skeleton (prologue, the switch, empty cases, epilogue). Each call
   carries 100-200 instructions' worth, which any model handles. The judge doesn't change: the function matches
   when objdiff says 100% for all of it.
3. **Several agents on one function, at once.** With parts, a big function can be held in parts: each agent claims
   a part, works on it with compile_part, and its change stays when the function's score gets no worse and no
   other part's rows get worse (the compiler allocates registers over the whole function, so a case can move
   another's; such a change is refused, saying which part moved). Agents see each other's parts land, through the
   file, and notes per part. Briefs give each agent its part's assembly only.

Measure each step on that DS game's 25: how many get tried and how far, against the week before. Step 2 is what
matters most for free models: step 3 adds speed, and contention on one file, so it waits until 2 shows parts can be
worked on alone.

### Library decomps built locally

Local SDK archives can now be paired with explicitly supplied C and declarations, then tried by exact fingerprint
with the destination compiler. The next step: match library decomps and
libgcc/newlib, built on your computer with the project's compiler (libultra on N64, the PS2 SDK libraries), and try
them as twins. Library sources are fetched only when you ask, never bundled: SDK code is the console maker's
copyright.

### Names from another build you own

Many games have a second build with symbols: a demo, a prototype, or a port to another platform. C++ symbols say the
class and the parameter types, so carrying them over gives agents real signatures. Pair functions by the text and
constants they use, spread the pairs through the call graph, and let the user approve the names in bulk (as boundary
reports are approved now). Names only: matching is unaffected. It also names the classes `get_class` finds.

### C++ classes for PowerPC

The class recovery reads MIPS and x86 (Windows PC: RTTI's names, the classes of code built without it, and their
fields: done). Left: GameCube and Wii (CodeWarrior: the vtable pointer often isn't at offset 0), with a PowerPC
reader (`Arch.field_accesses`), in fork mode.

### A modding-ready check for the export

The export rebuilds the game byte for byte; mods need code that can move. Build a second copy with padding inserted
and report every address that is still hard-coded ("93% movable, 41 addresses left"). The matching build stays as
it is. A Windows PC project's rebuild puts each function back at the game's address in the user's copy
(tools/rebuild.py): moving code there needs its data delinked too, and a linker that lays the program out.

### Browser room contributions

Delivered: normal GUI hosts serve a member-authorized browser workspace from the existing
restricted room server; no headless command is required. Saved C, read-only context,
claims and host-judged checks use the common ROM/imported-project pipeline. The browser
never receives target bytes or disassembly, private app APIs, file browsing or MCP access.

Remaining: local target disassembly/diffs from each guest's own file across processors,
browser context editing, compiler diagnostics that exclude target data, streamed identity
checking for large disc images and console-specific extracted-program identities. These
browser-only features are **—** across consoles until their adapters are implemented;
desktop rooms retain their existing support. Current browser identity checking handles
raw files up to 256 MiB and cartridge byte-order normalization; compressed discs need the
host's matching extracted program instead.

### A list of open rooms (to investigate)

Asked for by a user: a page where you see the rooms running now and join one. Hosts would still host their own
rooms, as now (the host's decompdecomp is the room, through its tunnel or address); a small directory server would
only list the public rooms whose hosts chose to list them: the game's name and hash, the platform, % matched, members,
and the room's address, dropped when the host stops answering. Joining is unchanged: the guest's own copy of the game
must have the host's SHA-1, and only the work travels.

Before building it, find out:

- **Is it allowed?** What a public directory of rooms means legally for the project, in the places it's used: what
  it may show (titles are trademarks: name only, no box art or screenshots), whether listing a room makes the
  directory responsible for what the room does, and what notice-and-takedown it needs. Check that nothing a listed
  room sends is the game's code or data, for git projects too (forkroom.py: what a joining guest gets of the host's
  tree).
- **Who runs it, and for what cost.** The site is static (GitHub Pages), so the list needs a host of its own; a
  small worker with a heartbeat per room may be enough.
- **Abuse and privacy.** Listing is opt-in per room and off by default; a room on a plain address shows the host's
  IP, so only tunnelled rooms could be listed; room names need limits, and a host or the directory can delist one.

The answer goes here: build it, build part of it, or not, and why.

### Per-agent work selection (done)

The launcher and each worker card offer independent queue strategies, a best-match percentage ceiling and per-worker candidate-attempt limits.
Choices persist across sessions, apply to ROM/project mode and travel to the team host; automatic mode keeps
adaptive routing. Configured queues do not batch unfiltered neighbours.

## Later

- **Better look-alikes.** A second measure (edit distance over opcodes) next to today's, and shared runs of
  instructions, which find inlined helpers and macros. Needs PowerPC and x86 opcode readers.
- **Decoders for data formats.** Tools that decode N64 display lists, PS2 GS/VIF packets and GameCube GX command
  lists for agents.
- **The permuter's C, tidied.** A matching variant from decomp-permuter can be hard to read; a polishing helper
  could tidy it, still matching.
- **Polish in the drafts.** Methods of known classes are drafted as `Class::method` in the brief; the polishing pass
  doesn't use them yet.
- A Flatpak on the GNOME runtime.


Agent batches select the best scored continuation (baseline wins ties; all errors retain the starting C). Selection changes only the agent snapshot and displayed diff, never game files, attempts or judge verdicts, and is invalidated by the next submission, claim or context/settings change. Bounded stale-revision deltas avoid unnecessary full-source rereads; same-function edits remain serialized and stale edits never compile. Missing signedness/declaration-order selectors choose the next untried valid target. Compile/release feedback gives the remaining move-on budget, and repeated identical diffs recommend an applicable local family before another ineffective edit. Shared across ROM/imported modes.

Agents can call try_local_repairs to discover applicable repair families or test a named family list against one source revision, with six trials and the shared 60-second batch budget. Untried candidates rotate across families; normal claim limits and exact judges apply in ROM/project modes. Routine replies are compact; release/redraft handovers use the compact brief, and small whole-source edits receive one patch reminder per claim. A server cannot reclaim tokens already submitted by a CLI; patch savings depend on subsequent agent tool choices.

Agent-card matched and improved counters open project/worker-scoped, paged function lists with links to the function detail. List attribution follows the same current-match and historical-improvement rules as the counters. The native sort control has explicit focus styling and a compact layout.

Shared-header rejection trials are cached for up to 120 seconds in a bounded process-local cache, keyed by exact headers, project settings, matched members/targets, symbols and compile generation; identical concurrent trials coalesce. Only rejected edits are cached; accepted edits always run regression checks. This applies where edit_context is supported (ROM/shared-header coordination); imported source/header edits retain their ordinary project-build validation. Agent assembly reads can focus on a target symbol/anchor or the first available differing instruction (40-line cap), with ordinary paging as fallback. Anchor failures include bounded current-line/occurrence hints without fuzzy application, repeated stale deltas are suppressed, and family previews show actual changed lines.

Deterministic repairs can reuse a dead existing integer local for repeated member comparisons and try coordinated integer-address operand order for byte-pointer table loads on supported 32-bit processors. Both families run in the shared ROM/imported draft-repair pass and try_local_repairs, under existing trial/time limits and exact judges; they do not change compiler settings.
Conservative shared-header declaration preflight rejects clear primitive extern conflicts before regression compiles; uncertain declarations still use the ordinary judge. Agents can inspect later differing instructions or a bounded post-entry assembly page, and brief/draft section aliases avoid recovery turns. Bounded compiler experiments test up to six source/profile pairs against one revision, retain retrievable process-local evidence and never apply diagnostic matches. Imported trials restore source/compiler state and are not cached because external build inputs can change.

Volatile absolute-register lvalues can be tested as explicit uncached MIPS addresses; scalar reads and ordinary RAM globals are excluded. Local families receive stable mismatch-based advisory ordering, never filtering or bypassing exact judges. Near-match claim briefs retain full candidate C with a bounded target listing and full-detail tools. Compiler experiments accept current (including function-range flags, or imported per-file compilers where supported). Address/literal temporaries avoid splitting C89 declaration initializers.

Global permuter instruction, time-per-turn and turn-count limits are adjustable in
Settings, persisted and applied to new turns in both ROM and imported projects.

Captured-repair families coordinate dropping 2–3 simple trailing call arguments with local prototypes, literal struct-pointer byte offsets, and flat switch-case call distribution. They share existing ROM/imported local-repair budgets and exact judges. Process-local, scope-keyed observations demote families after repeated identical scored instruction diffs, without skipping compiles or normalizing source. Tool schemas distinguish compiler profiles from repair families; full/matched section aliases and actionable redraft guidance avoid recovery turns.

Agent batches can verify and cache missing baseline instruction evidence per claim/source/target/settings scope; imported verification restores the project through the diagnostic judge. A separate per-agent identical-output limit defaults to 8 (0 disables), releases the lease while retaining best source, and leaves no-improvement limits independent. Repeated batch warnings are compact with full warnings available on demand. Typed family selectors separate next/anchor/declaration modes, and paired adjacent-load scheduling candidates retain both loads and require the ordinary exact judge.
Agent cards have persistent metric reset baselines, total reported tokens and time
since the last improvement, retaining code, history and lifetime model accounting.

Repeated-output limits now remember bounded fingerprints across a claim and reset the repeat streak only for genuinely novel scored instruction output, catching alternating old results. Baseline summaries distinguish verified, scored and unverified evidence. Unknown inline repair categories map to other while retaining their original label and code submission. Shared local families can narrow array indices, split scalar floating-point lifetimes, and capture repeated pointer fields before aliasing stores; ordinary exact judges and existing budgets remain mandatory.

Function lists show check/improvement recency. Agent work selection supports oldest
checked first and a recorded-model untried filter in ROM and imported projects.

The shared deterministic repair pass now tries bounded unused scalar removal, scalar/pointer storage and adjacent local declaration order on supported 32-bit ROM/imported projects, preserving line layout and using normal exact judges. Close ROM misses rotate cached debug and optimization probes, two per pass, with manual compiler approval respected. Imported compiler flags remain owned by their build.

### Local source preparation and renewed free searches

¹ Imported builds retain their own declarations headers; reference-header injection remains a planned gap.

Opt-in Settings → Local source preparation reads explicitly mapped local reference bodies, preserves formatting,
applies supplied numeric retail macros and judges each seed before saving it. ROM mode installs improved seeds
with private per-function declarations; imported mode uses its existing build headers and objdiff judge. Both
modes share bounded preparation, cached trials and diff-focused permutation. External input changes renew bounded
random turns; the permuter's own improvements do not. See [the setup guide](docs/LOCAL_SOURCE_PREPARATION.md).

Remaining gap: imported projects cannot automatically inject a reference declarations header into the project
build. Their body must already compile against the project's own headers. This needs a reviewed header edit and
regression checking of affected units before it can become automatic.


Agent work selection now reaches small-function batches through the shared scheduler (including percentage,
model-history, attempt and lease filters). Paused queues wait without starting a paid session. Cooldowns and
local repair history use the function's compiler range, effective unit header, referenced symbols and target,
so unrelated symbols, other-unit contexts and progress bookkeeping do not reopen old work. Imported projects
also watch source includes and shared build configuration; unresolved includes conservatively watch include/.
Actual shared header/build changes still invalidate the corresponding evidence. Judged local misses persist
across claims and app restarts; failed builds remain retryable and explicit patch trials remain available.

Agent-reported historical candidate concerns are retained with bounded compiled alternatives and shown in both
ROM and imported briefs via get_function(section="quality"). Existing notes are labelled unanchored reports.
These are not semantic proofs: best scores, saved best C and the exact judge remain unchanged. Alternatives must
be rejudged in their current context. Custom generated dependencies outside the observed build/include files
still need richer build-system dependency records.

Shared deterministic repairs can stage a scalar store's byte address, route repeated pointer-to-integer casts
through an inline helper on supported 32-bit processors, remove/shrink unreferenced local padding, and narrow
an ordinary local when its existing uses already contain narrow casts. ROM and imported projects use the same
bounded draft/local-family pipelines and ordinary exact judges. These remain source hypotheses, not semantic
proofs or automatic compiler-setting changes; volatile, escaping and ambiguous cases are left alone.
Measured MIPS stack-only diffs can also propose two small missing-padding hypotheses when the prologue,
epilogue and every changed saved-register slot agree on the same positive frame delta. The optional iterative
phase uses its existing budget; agent local trials require measured evidence for their current source. Other
processors still use padding removal/shrinking until they have a corresponding frame reader.

A function drawer can launch a bounded deep permuter job for one saved, scored candidate in ROM or imported
projects. It bypasses the background queue's size and turn filters, visits the generated local repair list,
then uses ordinary random rewrites. Time/compile limits, progress, cancellation, normal judges and immediate
saving remain mandatory. Scoped misses survive another deep run; failed compiles stay retryable and cached
improvements must be rejudged. This does not claim to exhaust all possible C programs or compiler options.

Repair preflight filters provable plain-prototype arity errors in shared generated local families and ROM
permuter/reuse candidates, before the ordinary judges. Saved ROM GCC drafts get cached advisory warnings in
agent briefs and deep-search output. Planned: compiler-specific diagnostic flags for other backends and an
opt-in diagnostic build command for imported projects (their ordinary build diagnostics remain available).
Reuse discovery batches fingerprint names/cache reads and persists a separate cheap-discovery cursor; at most
half the remaining shared reuse time goes to that phase, leaving time for wider matching.

Project settings now offer a resumable deep permutation pass across unmatched functions in ROM and imported
projects. Each function's completed bounded turn is persisted with its source/build scope and budget. Unfinished
turns resume after restart; changed inputs become eligible again. Busy functions and candidates without scored C
wait without being marked complete. The pass uses the same local/random searches, claims and judges as a single
function, with a visible progress bar and stop button. No paid helper is launched.

Deep searches record trial counts and verified score reductions by rewrite family, with the complete random
rewrite lineage of each recorded improvement. The settings view shows current scoped queue counts and the
latest saved evidence; single-function output reports its phase and latest candidate. Tracking starts with
new trials; a final random rewrite can depend on earlier changes in its lineage. Results are evidence for
manual review and do not automatically reorder the search.

Agent cards show results first, with compact usage and timing, expandable details,
and a keyboard-accessible actions menu. The agent list scrolls with the page.

Exact-fingerprint partial C reuse now rejudges better saved candidates across local ROM and imported
projects, retaining only destination improvements and recording provenance separately from verified matches.
Trials are cached by candidate/target/context/settings, capped at 64 per pass and share existing search budgets.
Configured automatic cascades now queue compatible ROM and imported projects after new matches or improvements.
Exact transfers use the destination judge, independently of donor compiler settings. Wider similarity/template
search remains ROM-only; tracking dependencies outside an imported checkout remains planned. Imported reuse
snapshots local source/header/build inputs (at most 10,000 files / 32 MiB).

Cross-game transfer results use compact, responsive rows with separate provenance, result labels and score changes; recent partial improvements can be filtered independently.

Cross-game checks show discovery progress and the current donor/target through the shared job bar, with a separate check-budget bar. Per-project device preferences, quick/hour/overnight presets and Stop support manual budgets up to 24 hours and 1,000,000 checks in ROM and imported modes, including unfinished C.

Cross-game results can be collapsed per project without losing state on live refresh; Run now launches a dedicated, bounded cross-game check with progress and duplicate-run protection, in ROM and imported modes.

Deep-permuter trial measurements now retain scoring outcomes and elapsed judge time by rewrite family and phase.
Local follow-up candidates after a random improvement are counted separately, so their improvements and cost can
be evaluated without conflating them with initial repairs. Historic attempts remain in their existing counters;
new outcome and timing measurements begin when this version runs.

## Compiler profile cleanup in imported projects

ROM projects can consolidate rare profiles after exact byte comparisons and a final regression check. Imported
projects still need cleanup through their per-file build settings and objdiff unit checks, including stale-input
validation and rollback before any settings change is published. The app hides cleanup for these projects until
that path is implemented; their declared build commands must not be rewritten as ROM compiler ranges.

Project overview reads query SDK signatures by the game’s indexed fingerprints, reuse imported library reports, and prefer LibYAML’s safe loader for large compiler declaration maps. No judge or detection evidence changes.

### Compiler source-shape repairs

- Done: bounded short-array copy recognition, existing typed record accesses, lexical pointer scopes,
  synthetic one-shot wrapper alternatives, array-address sharing and paired conservative lifetime merges
  in the shared draft repair path. These are local compiler hypotheses, available to supported 32-bit
  ROM/project repair callers; incompatible compiler builtins fail through the normal judge.
- Done: one bounded regression branch in the persistent ROM/MIPS iterative repair phase, within its
  existing independent budget and rotation. Imported-project iterative search remains limited by the
  file-context adaptation already noted in `app/iterative_repairs.py`.
- Planned: infer new record layouts from verified multi-access evidence and adapt persistent iterative
  search to imported file contexts. Current repairs use existing simple record definitions only.

### Evidence-driven automatic repair search

- Done: persisted build-profile/mismatch outcome priorities in deterministic and random repair search,
  with minimum sample sizes, bounded nonzero weights and exploration retained.
- Done: target-aligned regional gains select the existing bounded regression branch in persistent
  ROM/MIPS iterative search and single-function deep search (both judge modes when rows are available).
- Done: bounded candidate-local inference of consistent scalar record fields in the shared draft path,
  including ROM and supported 32-bit imported-project callers. Shared layouts remain unchanged.
- Done: local, guarded triage of untouched repair examples, duplicate grouping and preserved manual reviews.
- Planned: broader real-game replay to establish whether inferred records or adaptive priorities yield
  more exact matches; the initial near-miss sample found no new matches from inferred records.

### Agent transcript efficiency

- Done: shared bounded pointer-chase reuse, independent scalar initialization order, terminal early
  returns and repeated member-store hypotheses, available to ROM/imported draft, permuter and local tools.
- Done: repair dialog batch/single-example compiler-output replay with explicit budgets, progress,
  cancellation and non-adopting results. Imported historical build input gaps remain as described below.
- Done: source-hash validation distinguishes cascaded provenance from the candidate that triggered it;
  random rewrite reports also validate the failed baseline hash before calling evidence direct.

- Done: the CLI preflight also executes read-only project_info through authenticated MCP before model
  launch; advertised tools alone cannot hide a failed helper/project association. Existing bounded
  lost-connection pausing from #194 remains in place.
- Done: declared byte-field F macros expand for the shared ROM/imported permuter input, including nested
  loads/stores. Unrecognised macro definitions and ordinary functions named F remain untouched.
- Done: bounded floating-product intermediate lifetime proposals in shared draft, permuter and agent
  local families. Existing casts/order remain explicit; loops, escaped locals and later live uses are excluded.
- Done: offline, bounded full-export replay for recorded ROM compiler profiles, with baseline/control
  validation and per-family rotation. Reports never adopt matches or alter example review state.
- Planned: standalone imported-project replay needs archived build inputs and historical headers; exports
  currently lack those inputs and are explicitly skipped rather than tested with guessed settings.

- Done: scoped diagnostic baseline preflight for generated agent batches, shared copy/scope/layout repair
  availability and adaptive priorities in ROM and supported imported-project callers. Explicit patches
  remain an escape path for correcting broken C; they still use the ordinary judge.
- Done: compact project-range summaries, normal-lease-preserving suppression of unchanged claim packets,
  explicit reasons for single-function batch fallbacks, and bounded retries for invalid authorization grants.
- Done: verified ROM agent symbol substitutions can complete recorded donor transfers using the existing
  unambiguous-provenance and independent-pair requirements. Imported transfer-origin support remains a gap;
  `repair_mappings.harvest` explicitly restricts this path to ROM projects.

- Windows compiler orphan cleanup after an app crash: add process birth verification and job-object ownership equivalent to the POSIX startup registry. Runtime timeout/tree termination is already supported.

Shared target discovery now covers ROM and imported objects, including assembly-only units, and local SDK
signatures. Explicit bounded cross-game passes populate the catalog; overview polling never crawls checkouts.
The results panel separates detected shared code, writable candidates, scaffolding blockers and verified
transfers. Imported function details expose the same grouped name hints. Native assembly units can receive
C candidates only when their existing build successfully probes the candidate object rule; references and
linked assembly remain unchanged. Projects without such a rule remain blocked and need a build adapter.
ROM → imported and imported → imported C are judged by the receiving project's objdiff configuration;
imported → ROM and ROM → ROM retain their link/byte judge. Cross-compiler exact candidates are permitted.

## Verified wrapper families and between-turn local repairs

Implemented: same-project small MIPS ROM wrapper reuse with one paired global-address substitution;
integer scalar-address lifetimes and ordered bitmask-store proposals in shared draft/permuter/agent paths;
and an opt-in bounded local pass after single-function agent near misses in both judge modes.
All proposals retain the normal judge and cache/budget gates.

Wrapper-family discovery on imported projects and non-MIPS targets: **—**. The initial index needs raw
ROM instructions plus resolved MIPS HI16/LO16/JAL relocation pairs. Imported objdiff units do not expose
that same target representation; an object-format adapter and equivalent relocation checks are needed.
Cross-project wrapper-family inference and multiple changing globals remain future work.

Transcript follow-ups: inherited-candidate declaration diagnostics/recovery, structured per-helper session
handoffs, credit-required model failures, target/supporting/full repair diffs and advisory pattern candidates
are implemented in the shared agent/repair pipeline. New bounded repair families use the existing local,
draft and deep-permuter paths and ordinary destination judges. Recovery only reuses unambiguous existing
single-line declarations; missing complex/multiline types still require an agent. Handoffs record exposed
notes/replies, not hidden reasoning, and never automatically execute suggested next steps.

- Done: local source preparation retains referenced mapped-file typedefs/macros; ROM preflight prioritizes bounded
  same-toolchain neighbour profiles; helper-only diffs explain target-call mappings without mutating symbols.
  Imported projects retain their own build headers/settings; no neighbour-profile override is applied to them.

## Library formatter repair learning

Implemented: approximate local SDK opcode leads, explicitly imported C macro dependencies, bounded
formatter source-shape combinations, target jump-table case order and historical-baseline warnings.
Shared C repairs reach ROM and imported-project judges through the existing draft/local/deep paths.
Library leads are advisory; they cannot rename code or count progress without the normal checks.
Target-table reading currently supports MIPS ROM assembly with bounded, mapped tables. Other
processors and imported object-mode table discovery: **—**, require their relocation/table adapters.
Approximate SDK hints in imported project mode: **—**, require reading the target unit object through
that mode's existing object-format adapter. Generic C source-shape repairs already reach that mode.

- Done: typed SDK math-call mapping hints, allocation-extent advisories, bounded post-preparation compiler checks,
  and first-error compiler summaries with private full logs. Imported builds retain their own compiler settings;
  allocation/prototype hints are advisory and never alter symbols or record layouts.

## Target-guided local scheduling repairs

Implemented: candidate-owned missing first-call arguments evidenced by MIPS delay-slot loads,
byte-address alias hypotheses, single-field records across distinct call results, inverse record
access shapes, direct boolean masks, split call-result locals and compatible dead-result reuse.
Field-load capture tests offset orders observed in MIPS target assembly. Candidates are bounded
and round-robin by family; two-family bundles and the existing iterative/deep frontier keep
interactions available. Every accepted change still passes the ordinary destination judge.

Generic source repairs reach ROM and imported-project mode on supported 32-bit processors.
MIPS call/load evidence requires target assembly; other-processor argument recovery and
64-bit record layouts: **—**, need ABI-aware argument and pointer-width adapters. Shared
headers, symbol maps and compiler profiles are not changed by these local hypotheses.

- Done: explicit coherent reference source/header alternatives, same-file private helper prototypes,
  selected static inlining helpers, missing-macro/mixed-typedef advisories and a single bounded stale-input retry.
  Original bit-pattern object initializers remain verbatim; extraction rejects call expressions posing as definitions.
  Imported builds keep their own headers/settings: automatic SDK replacement there is **—**, requires their
  build system's existing header compatibility workflow. SDK-version identity remains advisory, not inferred fact.

Agent work selection now explains every strategy in the dropdown and selected-option help. The shared
`no_compiling_c` strategy restricts automatic and named new claims to open functions with neither a scored
candidate nor a recorded successful compilation, in both project modes; existing claims continue normally.
This is historical compile evidence, not a fresh build preflight or a claim that the function cannot compile.

- Done: MIPS raw-word I/O profile priorities, proven-address/width MMIO expression candidates, I/O local-storage
  candidates, and aggregate copies using existing byte-only records. Shared diff/preparation advisories flag
  constant and recovery-branch discrepancies without editing them. Ordinary judges, budgets and compiler approval
  gates remain. Imported projects retain build-owned settings; analogous target evidence on other processors: **—**,
  requires processor/ABI adapters. Existing one-retry stale preparation handling is reused rather than widened.
Captured lifetime follow-ups: split scalar casts from constant bitwise operations, reuse a dead unsigned
shift result in a store outside loops/gotos, and split a same-type floating cast before a product while
preserving factor order. Integrated with shared draft/local/deep families and advisory repair pattern views.
Isolated private replays recovered two recorded SN64 compiler-output controls with the new families;
a third selected KMC case was already recovered by the existing typed-temporary family. Replay recovery
is a regression result, not a new game match. Mixed/context-dependent examples remain for review.

- Done: mapped-file preparation tries up to eight unambiguous referenced object definitions verbatim before
  its extern-only alternative; explicit definition selections and paired contexts remain supported. Named-buffer
  address candidates require an existing array declaration and raw MIPS address evidence. Typed runtime-helper
  call-only diagnostics, entry-context/NOP/record advisories, and rejected-preflight-suggestion recovery share
  ordinary judges and budgets. Other processors' address/entry evidence adapters: **—**, planned work; imported
  projects retain build-owned compiler profiles and ROM-only entry diagnostics do not alter their setup.
## Register-only local lifetime search

Shared draft, agent-local and deep searches offer bounded branch-tail locals, scalar call staging
and combined proposals for ordinary C in ROM and imported-project mode. Register-only diagnostics
prioritise these shapes. Search frontiers avoid expanding measured frame/code growth without
structural improvement, while retaining numeric improvements and ordinary exact judging. Generated
compile errors reuse input-scoped caches; agent summaries include failures and regressions.
Synthetic execution/search regressions are distinct from newly measured private-game recoveries.

Latest-match follow-ups: grouped build activity in repair evidence; same-width copied zero-test lifetime and
explicit pointer-return-call staging hypotheses in shared draft/local/deep search; actual multiline reference
macro dependency preservation; reference by-value aggregate and whole-structure snapshot diagnostics.
Unknown aggregate layouts/ABI shapes remain diagnostic-only. Macro invocations and original supporting types
are preserved rather than hand-expanded. Synthetic MIPS compiler controls cover the new lifetime families;
other processor/object judges retain the same shared dispatch and no acceptance criteria changed.

Private-context profile probes also cover prepared candidates that fail to improve under the current
flags (two probes within the existing shared budget). Their source/header/profile evidence is retained
for review; joint context/profile approval and automatic promotion remain future work.

- Done: bounded adjacent scalar prefix-result and postfix-store hypotheses feed shared
  draft, agent-local and deep-repair families. Pinned-compiler controls verify generated
  shapes; nonadjacent increment motion and automatic narrowing remain unsupported pending
  liveness and range evidence. No new full-game matches are attributed to this rule.

- Done: bounded arithmetic-before-call, explicitly typed element-store and local
  unsigned-short reuse hypotheses feed the shared repair pipeline. Matched callee
  types/prototypes and existing cross-game library leads expose attributed source
  declarations/macros for review in ROM and imported-project workflows. Live headers
  remain unchanged; adopting context still requires compilation and the normal judge.

## Typed pointer and paired declaration repairs

Shared draft, agent-local and deep paths extend pointer call staging and word-zero-test reuse,
add pointer-load inlining and CFG-safe pointer storage reuse, and offer a bounded declaration-order
cross product without requiring an improving first edit. Distinct plain struct-field stores can
swap only with memory-independent values. These ordinary C hypotheses reach ROM and imported
projects through their existing judges; uncertain aliasing/volatility stays with the agent.
Fixed-context replay distinguishes judge-only revisions from compiler/header changes. Private
linked replay recovered three already solved pointer/zero-test controls; an existing MMIO family
recovered a fourth. This is regression evidence, not new progress or measured token savings.

### Prepared source/profile review and data diagnostics

- Exact private source/header/profile probes can be reviewed together in local ROM projects. Applying
  rechecks the ordinary byte/data judge, claims and stale inputs, then saves a private header and
  function-only profile atomically. Imported builds retain their own header/profile ownership.
- MIPS link diagnostics distinguish exact linked instructions from differing initialized data and show
  the differing address range. Other linker adapters retain their current exact verdict; structured
  instruction/data detail remains an adapter gap.
- Original typed preparation still runs before broad permutation. At most two named-array copy
  hypotheses require an existing plain-int-array declaration, exact extent and verified ROM values.
- Private preparation history retains source/header hashes, profiles and failure categories, with
  source/header/profile/target/symbol/build scoped invalidation; it is review evidence, not progress.

### Smaller matching conversations

- Done: compiler-trial reads expose only the selected C/profile and context ID;
  complete private configuration stays in the internal diagnostic ledger.
- Done: multi-function claims hydrate the first function and retain a compact
  queue; queued briefs and full assembly remain explicitly available. Assembly
  defaults to the first mismatch, with a bounded body fallback and explicit paging.
- Done: explicit handwritten batches run their requested variants. Automatic local repairs
  are explicit tools or the optional after-submission setting; they never replace a batch.
  Existing failed-trial ledgers remain authoritative.
- Done: CLI and API conversations rotate after 80 tools or 160,000 tool-response
  characters, once active tools finish. Structured handoffs retain candidate
  revisions, remaining experiments, history and notes; claims and source survive.

## Raw MIPS address repairs

- Done: numeric effective addresses accompany symbolic ROM diff operands and agent
  mismatch feedback, derived from the target and fully linked candidate bytes.
- Done: bounded expression-local literal/byte-array high-half repairs, including
  independent pairs, reach draft, agent-local, deep and autopilot address passes.
  Synthetic guards cover signed offsets, clobbers, control flow, endianness, missing
  words, ambiguous destinations, macros, typed-pointer scaling and unrelated C.
- Verified privately: V-Rally PAL's historical `func_8000F660` near-miss replays
  80 -> 80 for either single, 80 -> 0 for the pair under the normal byte judge.
  No extra live matches or measured AI-token savings are claimed.
- Planned: analogous raw address diagnostics for processors other than MIPS;
  these proposals currently require MIPS instruction evidence.

### Matching efficiency feedback

- Done: persistent claim/build-scoped recent-score windows hand back work after
  24 scored trials with at most 1% recent gain, retaining best C and actionable
  notes. Larger explicit work budgets extend the minimum; changed build inputs
  invalidate the window. New sources remain eligible for later work.
- Done: compile replies summarize the first differing region and mismatch category;
  independent batches report selected revision, claim state and applied patch IDs.
- Done: existing compiler/focus-specific repair learning now includes measured
  trial time, with bounded weights and original-queue exploration preserved.
- Done: agent Details shows completed-session compiles, helper seconds and separate
  token categories per recorded match, grouped by model/build/work-selection
  cohort with observed function sizes. Unsuccessful work stays in the denominator;
  missing provider usage suppresses token averages. Legacy unscoped sessions are
  excluded from these comparisons. This is not exact per-function billing.

### Prepared-source investigation evidence

Local preparation now retains ranked compiled call/field/control evidence, source identities (including
coherent alternative files), explicit rejected-lead reasons and a compiled target/candidate difference
view. Ranking never assigns canonical names or changes source branches automatically. Unchanged rejected
ROM trials are skipped; changed source/header/profile/target/build inputs renew evidence.

MIPS incoming-register diagnostics recognize floating aliases and mixed `f12`/`a0` entry usage, show
available caller setup in agent briefs and distinguish incoming stack slots from C parameter counts.
Other processors need equivalent ABI adapters; imported builds currently expose measured scores/family
results without compiled structural differences. Their original build still owns compiler/header settings.

Repeated prepared register-only misses receive targeted lifetime/type/helper-context guidance. Local family
results exclude cached repeats, separate compiler profiles and retain denominators; after 32 trials a family
can influence preparation order with original-order exploration retained. These observations are not causal
model/compiler rankings, and no family or unmatched function is automatically excluded.

### Navigation loading and response ownership

- Done: project and function navigation clears stale identities/data immediately,
  displays a loading bar and supports retry. Overview and map reads start in
  parallel. Abandoned primary GETs are cancelled where supported; generation
  checks still reject responses that arrive after cancellation or A → B → A visits.
- Done: a pending function read cannot block a different function. Delayed map,
  compiler-column, statistics and diff responses cannot repaint a later visit.
  Compiler/deep-search jobs continue on the backend while their UI callbacks stay
  tied to the initiating function view; new views do not inherit old progress.
- Done: unfinished imported projects can be closed directly from setup, keeping
  the repository folder and all files on disk for a later import.

- Done: Agents & models shows the local MCP endpoint, transport and port, with
  project-specific tool descriptions/schemas. Manual clients can create copyable,
  persistent project-scoped credentials and revoke them without exposing the GUI
  secret or saving plaintext bearer tokens. External sessions remain client-managed.

### Structured lifetime repair and durable match promotion

- Done: shared bounded repairs forward a scalar through an existing same-type record field,
  separate one explicitly narrowed definition from later full-width uses, and sink a zero into
  the non-assigning arm of a simple branch. ROM and imported-project C repair pipelines share
  these candidates; unsupported/ambiguous syntax is skipped. Register-only diffs prioritize them.
- Done: conditional back-edge recovery handles nested switches and internal/outgoing gotos,
  refusing external entries and changed break/continue targets. Every candidate uses the usual judge.
- Done: random search retains quality-unverified starting checkpoints in explicit quality evidence
  and filters newly introduced nested compound-update chains. This is not an equivalence checker.
- Done: ROM progress import publishes a newly matched best draft's source and manifest together
  before acknowledging dirty writes; concurrent editor contributions retain precedence. Imported
  repository projects already edit their source tree directly and do not use this ROM save format.
- Done: public imports recognise portable progress bundled with standalone builds.
  ROM selection restores and reverifies saved work through the existing save
  pipeline, preserving the checkout and following the restored project identity.
  Open a saved project also accepts a repository root containing progress/.

Local browser workers can explicitly pair for manual KMC ROM tasks and host-rejudged plain-C submissions.
The bridge is origin/project scoped with expiring capabilities, one-use tasks and transaction-time stale/revoked
checks. Imported object projects, automatic room claims, richer source preprocessing and non-KMC compilers
remain planned: the initial browser toolchain has no matching build-context adapter for them.

- Done: concurrent deep passes publish function-attributed activity snapshots with
  bounded recent output and eight recent completions. Settings shows stable,
  clickable function cards with phase, rewrite family, match changes, trial budgets
  and improvements; combined diagnostics remain available in a collapsible log.
- Done: room members can run function-level and project-wide deep passes, taking
  and renewing host claims with separate identities for concurrent functions.
  ROM candidates compile locally; improvements use the existing room protocol and
  exact matches require host confirmation before local acceptance. Imported-room
  candidates use the host's build/judge. Compiler-profile changes remain host-owned.

- Done: RetroReversing fork builds show version and source commit in the sidebar,
  settings, bug-report text and startup logs. macOS/Linux/Windows packages embed
  their source identity and reject packaging uncommitted tracked changes.
- Done: autosave diagnostics put the actual exception, progress path, repair advice
  and fork build on the first log line, retaining the full traceback. Failed
  projects back off for 30 seconds while other projects continue synchronizing.
- Done: Settings accepts a GitHub fork URL and branch as the update source,
  validates it before saving, and displays the selected repository. Explicit main
  follows commits; existing release installs retain their previous behavior.
  Saving changes no code and does not restart; updates retain normal review and
  package/platform restrictions. Invalid sources leave the working source intact.
- Done: deep-pass summary includes matched, improved and active function counts;
  start/stop share one visible action with reset beside it. Improvement columns
  sort numerically or by name, retain order during refreshes, and show adjacent
  before/after scores plus score reduction. Local macOS builds can deliver a
  verified .app directly with --app-only instead of making an archive.

- Done: deep-pass sortable headings use the neutral Functions-table typography
  and ascending/descending arrows, with the active column highlighted in ink.
- Done: one sortable Rewrite performance table replaces duplicate rewrite and
  outcome summaries. Per-family details retain diagnostic counts; older missing
  timing/outcome data stays unavailable, and partial timing is labelled. CSV export
  includes every family in the selected order, detailed measured outcomes, timing
  coverage, and project/platform/version/build metadata without source or ROM data.

- Done: deep-pass activity cards default to active only, retain expandable output when sorted, and show measured elapsed duration for active and completed functions.

- Done: parallel deep searches use per-function runner credentials outside rooms too, preventing token invalidation between workers and the background permuter.
### Same-name verified source reuse

- Done: a shared bounded Autopilot/cascade phase tries meaningful same-name matched C across
  compatible ROM and imported projects before broader similarity search. Names are discovery hints,
  not identity/ABI or match proofs. Generated address names are excluded; targets and donor projects rotate.
- Done: imported donors use read-only, checkout-bounded includes and literal build hints. Missing
  conditional headers become preprocessor errors rather than guessed declarations. Supported pinned
  destination compilers produce a compact declaration closure and can try one source-declared numeric
  version boundary coherently across headers/body. No make/configure or model calls are introduced.
- Done: candidate/context/profile/target/linker/build inputs scope cached trials. ROM header promotion
  does not repeat an unchanged self-contained donor trial; imported destinations retain their native
  headers and normal objdiff build. Only the normal exact verdict creates a match.
- Planned: preprocessing adapters for drivers without a supported `-c` → `-E` mode, automatically
  supplying foreign static helpers/initialized data, and preparing assembly-only imported units from
  name-only leads. Those cases remain visible skips or need explicit existing source-preparation setup.

- Done: room deep searches publish one measured turn summary instead of phase-release boilerplate; latest summaries replace repeats per runner/function, and obsolete lease notes are omitted from function views and briefs without deleting history.

- Done: per-agent Work settings can omit assembly byte/address presentation columns from claims, briefs, full function reads, redrafts and assembly pages; labels and operands remain intact across ROM and imported projects.

- Done: metrics CSV export saves through the private app API to Downloads with a visible saved path or error, avoiding embedded-webview Blob downloads. Recent deep-search cards distinguish completed turns from active workers and expose failure reasons.

- Done: transcript tool cards show compact argument previews and group requests with rendered responses using recorded call IDs; unambiguous legacy exchanges pair across pages while ambiguous concurrent legacy calls stay separate.
- Done: agent improvement lists correctly convert match ratios to percentages and label the current total match explicitly, distinguishing it from the agent’s historical improvement.

- Done: one tool-schema catalog serves transcripts, manual MCP setup, global agent tool settings and per-agent controls. Optional tool switches filter authenticated MCP discovery and API schemas, with dispatch enforcement for cached calls; essential claim/compile/release tools stay available. Persistent local usage counters include surviving historical transcript requests once, retain future calls across restarts/removal, and identify tools with zero recorded use without claiming complete pre-tracking coverage.
- Done: manual MCP connection setup is collapsed by default in Agents & models, using the shared keyboard-accessible disclosure styling.

- Done: global and per-agent initial-prompt templates (ROM/imported/polishing) and app-instruction overrides support preview, inheritance and reset. Sessions freeze their effective templates; authenticated MCP initialization/discovery and API system messages use the selected instructions, transcripts record the effective text, and prompt profiles separate efficiency cohorts. Project instructions and continuation context remain appended.
- Done: agent cards show one reported-usage line (unknown usage is not zero), one agent-scoped compile total, current function match/instruction metadata, and a clickable distinct-functions-worked-on total that respects metric resets.

### Count-lifetime repair coverage

- Done: shared mismatch-category/position diagnostics in ROM and imported Diff views,
  bounded structural alternatives, and MIPS count-phase/guard-width hypotheses through
  shared drafts, agent local experiments, iterative and deep repair paths.
- Planned: validate count-load scheduling and temporary-width adapters on non-MIPS
  compilers before enabling these compiler-facing source families there. Diagnostics
  and frontier retention already apply across processors; the MIPS replay is not a
  claim of matching gains on other architectures.

### Compiled C line associations

- Done: the C editor marks differing/extra/moved instructions red and operand changes yellow, with line-number buttons showing target/compiled instructions. Compile responses give agents bounded evidence from the same source map. Associations come from the exact scored object, with unchanged compiler flags; edits and function navigation invalidate editor highlights. Shared ELF DWARF 1/2+ and ECOFF readers serve ROM and imported projects; imported file lines are translated to the displayed function fragment and stale objects are rejected.
- Planned: CodeView/COFF, STABS and relocatable debug tables with overlapping executable sections. Unsupported or absent line information is shown explicitly; target-only instructions are never assigned a guessed C line. Optimized line associations are advisory, not causal proof.
### Expression recipes and bounded value proofs

- Done: token-level frozen-output ablation separates independent edits on the same line.
- Done: private compiler-profile expression recipes learned from exact connected deep-search
  edges, accepted local experiments and iterative-search victories; reused in deep/local search and optional iterative repairs.
- Done: shared draft repairs prove intervals for straight-line signed integer locals before
  proposing narrower storage, with compiler ABI checks and the ordinary exact judge.
- Planned: CFG-aware interval joins and loop invariants. Branches and unknown statements are
  deliberately rejected rather than assigned speculative bounds.
- Planned: train recipes from reviewed historical repair examples only after a fresh ordinary
  exact verdict. Frozen-control replay alone must not train acceptance evidence.

- Done: Repair examples puts compact source-repair cards and filters first, with candidate concerns, local testing/analysis and exports in separate disclosures. Cards preview the captured explanation and open directly onto source changes; coding-agent setup loads only when its investigation section is expanded. Review statuses use plain labels, form controls stack on narrow windows, and per-example tests reveal their progress section automatically.
- Done: session transcripts distinguish app-only connection probes from tools exposed to the agent, including legacy pre-prompt probes without rewriting saved events. New probe calls do not increment agent tool-use counters. CLI and API matching sessions preload bounded project facts and one ordinary coordinated claim; prompt previews never claim, and polishing keeps its own file-claim flow. Reusable matching knowledge has explicit per-function note guidance, allows general known-compiler behavior tips, and pending single-model proposals can be accepted or rejected manually without changing the two-model automatic acceptance rule.

### Post-match repair analysis and cost evidence

- Done: bounded automatic frozen-output ablation for new supported captures, a Repair examples toggle,
  persisted reports, and source-free summary exports; no progress adoption or review changes.
- Done: paired native-GCC phase comparisons gated on unchanged compiler output, mismatch-shape-specific
  exact repair sequence hints, and per-profile/focus compile and elapsed-trial-time efficiency rates.
- Planned: copied-build replay adapters for imported projects and phase diagnostics for other compiler
  backends. They explicitly skip instead of using guessed standalone build settings. Timing currently
  measures summed trial elapsed time; per-child CPU accounting across compiler runtimes remains planned.

### Repair capture follow-ups

- Done: bounded later scalar declaration swaps, typed clamp and record-pointer load staging, independent loop update order and call postincrement placement in shared draft, autopilot, deep and explicit local repair paths. Every candidate uses the ordinary judge.
- Done: missing call arguments use known matched callee signatures and unambiguous incoming caller parameters. Address-taken scalar buffers grow only from compatible, bounded contiguous constant-index callee writes. Called matched helper changes refresh local retry evidence.
- Done: single-line macro directives and dependent statements remain protected while unaffected regions can receive repairs; unsupported preprocessing receives an explicit diagnostic.
- Planned: preprocessing-backed conditional/multiline macro repairs, CFG-aware dynamic callee write extents, and captured callee bodies for frozen historical replay. Unknown field layouts/signatures, ambiguous arguments and pointer aliases deliberately skip these narrow families. Shared C hypotheses reach ROM and imported projects across processors; compiler-specific matching gains require ordinary verdicts and are not inferred from source-pattern coverage.

- Done: the update dialog offers packaged-app users source checkout instructions, Git/uv prerequisite links, a new destination path and an explicit background Git download from their configured repository/branch. Completion supplies terminal commands and explains the development copy's separate project home. Downloads never install, launch or replace the packaged app, and existing destinations are rejected.

- Done: restarted matching sessions receive a complete renewed-claim brief. Room clients reconcile their own stale local leases only after a successful host grant, transactionally in ROM and imported project flows; host failures and other workers' claims remain intact. Existing batched tasks can be renewed by name without being mistaken for a new conflicting claim.

- Done: selective branch pointer lifetime combinations and declaration-order bridges in shared C candidate
  repairs, with guarded terminal switch-tail distribution and advisory repeated-call shortfall clues.
  ROM and imported candidates use their unchanged ordinary judge. Unsupported control/macros stay unchanged.

### Decision and address repair hypotheses

- Done: bounded MIPS halfword arithmetic, single-case switch, shared flag-guard join and
  selector/address staging through shared drafts and local/iterative/deep search. Pointer
  liveness, macro/control-flow exclusions and the ordinary exact judge gate candidates.
- Done: promising ROM candidates prioritize debug-profile alternatives; compact register-only
  advice and manual-connection claim cleanup shorten investigation overhead.
- Planned: validate these source families on non-MIPS compiler adapters before enabling them.
  Imported projects share MIPS source repairs but retain build-owned compiler settings;
  automatic standalone profile adoption is ROM-only.

- Done: bounded repair combinations retain equal-score deep-search alternatives, scoped cached
  local diff evidence and family paths. The optional iterative phase persists paths and gives
  measured structure gains a limited follow-up burst without dropping pending work. Correct-region
  damage ranks structural alternatives without excluding the numeric winner or changing the judge.
- Done: Repair examples can benchmark multi-step frozen-control recovery, reporting compiles,
  elapsed time, first recovery and family path. Deep activity and iterative notes report bounded
  exhaustion/budgets and failed or pruned work. Imported standalone replay still requires a copied
  build adapter; live imported deep search uses its existing normal judge.

- Done: the update dialog source-download section uses the normal vertical content layout rather than the compact progress-bar class, so instructions and destination controls remain fully visible.
### Repair search measurement and combinations

- Done: two bounded flat-score family combination probes, retained exact alternatives in
  optional frozen benchmarks, and preservation of remaining parent waves after an exact hit.
- Done: full-scope iterative cache keys and uncached iterative timing/outcome training of
  adaptive priorities, with smoothed exact throughput and exploration retained.
- Done: reusable unresolved-case batch benchmark with equal budgets, source-free reports,
  first-exact cost and measured baseline/best scores. Copied evaluator required; no adoption.
- Validated: eight private unresolved N64 functions, 128 candidate trials, no matches or
  score improvements. This does not establish gains on other consoles.
- Planned: copied imported-build benchmark adapters and broader independent unresolved
  cohorts; the current iterative autopilot remains MIPS ROM-only. Shared measurement and
  policy helpers remain usable by existing supported local/deep paths.

- Done: saved-file verification uses a small reserved header spinner instead of a transient page banner. Details, progress and pause/resume are available on demand without shifting project content; reduced-motion and paused states are respected.

### Agent transcript efficiency follow-ups

- Done: feedback distinguishes recovery from beating the saved best; agents can explicitly select saved-best or last-compiling C for continuation without a compile, source-file change or verdict.
- Done: claim exploration budgets start from the inherited pre-attempt best, so recovery from a worse rewrite does not perpetually renew the allowance. Missing historical baselines retain the legacy counter.
- Done: explicit reasoned move-on releases bypass the one-time negotiation; compile responses explain the policy. Session function counts are upper limits in default prompts.
- Done: compiler advice identifies bounded candidate-local C89 declaration suspects and offers explicit recovery. Raw provider usage accounting is grouped in a collapsed app-only transcript section; source events and usage totals remain intact.

- Done: agent C-line mismatch evidence uses bounded Markdown rows with readable change names and Expected/Current columns, escaped code cells, explicit omissions and unlocated differences. Compact feedback retains the whole section; transcripts render escaped pipes and backtick-containing code without breaking table columns.

- Done: the update dialog separates installed/available builds, packaged releases and source checkout into responsive sections. Source checkout has a native parent-folder Browse control, full-width path field, styled download action and collapsed setup instructions; the running build commit fills missing install metadata.

- Done: repeating completed deep passes binds the project ID correctly, avoiding a server error on start. Regression coverage verifies only open-function pass records in the selected project are reset; saved improvements and other projects remain intact.

- Done: progress manifests round-trip compiler assembly-local labels such as .L00100380 in symbol metadata. C function/layout names remain strict identifiers; path-like or malformed symbols are rejected before files are changed.

Agent transcript follow-ups: late release notes are retained after automatic handoff without changing
another worker's claim; continuation instructions respect claim ownership. Compact feedback omits repeated
C-line tables for unchanged instruction diffs and labels proven relocation-rendering pairs separately from
instruction mismatches. Target assembly pages provide explicit end-exclusive ranges and next-page calls.
Reference typedef extraction excludes masked preprocessor prefixes to keep supplied declarations intact.

### Source-context CPU stages and export follow-ups

- Done: bounded actual helper-inline and same-unit callee-context trials, original complete local declaration order, and combined helper/order candidates before broader source permutations. Shared C logic reaches configured source preparation on every supported processor; build-owned projects keep only candidates that preserve their owned headers.
- Done: measured MIPS saved-pointer lifetimes, explicit commented retail if/else stores backed by target symbols, and repeated neighboring-float-element binding repairs. All remain candidate hypotheses under the unchanged judge, not source-identity verdicts.
- Done: newly matched source-preparation/deep-search functions can spend their remaining bounded budget on export repair. ROM-verified strings combine with macro arithmetic/scalar references, retaining scheduling-sensitive pools; prefix normalization still needs the standalone rebuild.
- Planned: non-MIPS saved-pointer readers and constant-operand syntax; imported-build private-header publication and object-layout/export adapters. The MIPS-specific readers cover N64/PS1/PS2 ROM work; absent readers produce no candidate rather than guessed register/layout facts.
- Planned: reconstruct entirely absent retail cases and enum revisions from CFG/data evidence. Current CPU retail-branch recovery restores only explicit supplied commented conditions with available declarations; arbitrary missing control flow stays outside automatic adoption.

- Done: shared target-block/whole-function repair ranking, bounded regional follow-up
  combinations, captured single/pair output ablation, and compact agent block reports.
  Generic search and replay are shared across processors and modes; automatic iterative
  autopilot retains its existing linked-MIPS-only scope. Other modes require a copied
  evaluator for composition replay; there is no inferred C-to-instruction source map.

- Done: a measured missing target call can restore one complete leading conditional from an already supplied machine draft into a typed reference candidate. Parameter names, declarations, macros and actual prototypes are copied coherently; unknown calls, side-effecting initializers and unbounded control flow are rejected. This is a candidate under the judge, not arbitrary CFG synthesis.

Recent repair-example automation: shared source generators now offer sibling-local scope merging,
CFG-checked narrow loop-exit value reuse, typed float argument staging before integer casts,
word-sized multi-use scalar-chain inlining, and unescaped pointer updates after error returns.
Both ROM and object-project source consumers use the shared draft/local-search registry and existing
trial budgets. ROM donor reuse reserves a slot for agreeing paired HI/LO global mappings before
raw-address guesses. Object-project donor remapping still needs its existing relocation/file-context
adapter; these source families do not change that limitation or any match verdict.

- Done: N64 late-code detection completes a frame prologue accidentally included in
  alignment after the last detected return. Recovery is bounded to valid instructions,
  a matching stack-pop return and the next known ROM/RAM segment; reimport preserves
  unchanged target metadata and progress. It is not arbitrary binary-gap code discovery.

- CPU source-context follow-up: existing same-file definitions precede extern-only variants;
  measured private ABI/extra epilogue routes and implicit-int/source identity warnings are available.
  Supplied raw guard fields can use actual simple scalar record layouts. Complete source literal/target ROM-string disagreement is also advisory. Complex nested compiler-backed
  member layout recovery, source candidate ranking by string identity, and compile/extract of whole translation
  units with private entry/clobber conventions remain planned. Imported builds retain header ownership;
  their whole-unit integration must preserve their build's judge rather than the ROM single-entry path.


- Done: MIPS ROM binary-gap audits with bounded incoming-transfer and frame/return evidence,
  shared incomplete-target warnings, and a narrow migration for the retired automatic
  fall-through assembly verdict. Warnings do not pause functions or shrink the denominator.
- Planned: non-MIPS boundary readers and object-project segment adapters for gap audits;
  EE-specific instruction decoding, function-pointer-only entry evidence and code/data
  subsegment audits. Existing MIPS diagnostics remain advisory when a decoder cannot close flow.

### Finish near matches

An explicit cancellable pass reuses iterative repair frontiers with a separate user
compile/time allowance, oldest-visit fairness, closeness ordering for fresh inputs and
matches-per-minute reporting. It skips claimed functions and resumes pending paths on
later runs. Current adapter: MIPS ROM projects. Planned: imported objdiff projects and
other ROM architectures need the same file-context/linked-feedback adapter as iterative
search; their per-function local repairs remain available. Shared candidate ranking is
architecture-independent; current-exact-caller prototype reuse requires current recorded
ROM verification and does not infer imported-build verification.

Installed-compiler comparison has an explicit per-run auto-apply option. Each completed function's
best profile is revalidated through the normal function-only apply path; changed inputs and busy
functions retain reviewable skip reasons, and applied outcomes are saved with comparison history.
The comparison action toggles to Stop while running. Its two determinate progress bars replace the
redundant loading text and animated workbar. This uses the existing ROM/MIPS compiler-map scope;
object-project compiler comparisons remain limited by their build-owned per-file adapters.

### ROM reference audits and export context retention

- Done: MIPS read-only pointer-word evidence and unowned mapped-segment interiors;
  incoming references are reported separately from pointer hypotheses. Probe limits
  are visible; no pointer or interior lead is auto-adopted.
- Done: narrowly bounded N64 direct-call, whole-gap, balanced-frame recovery;
  unchanged private compiler contexts survive reimport, including recorded-history
  recovery when the historical header is still current for its private unit.
- Done: ROM repository sync rechecks exact-attempt header alternatives and isolates
  plain independent helper bodies. Matching-only ELF32 MIPS entry extraction handles
  scattered pools; every compile is rejudged and complete-image verification remains.
- Planned: non-MIPS entry extraction and equivalent conservative import recovery for
  other consoles, richer callback/table provenance, instruction-level data references,
  overlay-bank identity and explicit reviewed adoption of weaker audit candidates.
Finish near matches now has bounded, output-preserving native-GCC def/use traces and six-candidate
traced bundles, plus verified same-unit private-helper/static-constant trials in its MIPS ROM adapter.
Stored traces also feed local repair batches. Held-out replay approvals gate experimental adaptive
boosts and broad focus promotion. Remaining: imported-unit verification/file-context adapters,
non-native/non-GCC dump readers, full translation-unit/private-clobber context, initialized arrays and
exact original physical helper-line provenance. These gaps do not permit guessed layouts or ABI types.

- Target-backed independent-address materialization is available to MIPS ROM iterative
  search, local agent repairs and the bounded pre-permuter pass. Imported project builds
  need a build-owned alias/link adapter; other architectures need their address-pair
  reader before this hypothesis can reach them. The first six-case controlled cohort
  gained one closer draft, with no newly exact function.
## Headless browser serving

Implemented: `decomp serve --port N` runs the shared ROM/imported-project engine on localhost without a native window or tray. Terminal-issued login codes grant separate, bounded HttpOnly browser sessions; host/origin/write guards and agent/project capability scopes remain in force. Sign-out revokes that session; updates preserve headless mode. Browser uploads work and local picker actions accept server-side paths instead of invoking a desktop chooser.

Planned: explicit LAN/reverse-proxy binding with TLS deployment guidance, configurable multi-user access and browser-based browsing of server directories. Native file-manager/desktop actions have no browser equivalent in this initial mode.

## Durable diagnostic evidence

Implemented: new compiler-trial records survive restart and can be read by other agents in the same
local project, with historical source/profile and optional shared-header evidence. Repair discovery
resumes cached pure stages under a cooperative budget; mutable project donors are read afresh.

Planned: transfer diagnostic trial evidence between separate room participant databases, with explicit
provenance and bounded source/header payloads. A local trial reference alone cannot retrieve remote
records or reconstruct missing imported translation-unit inputs.


### Historical recovery and conservative repair learning

- Done: explicit cancellable local-ROM saved-match reconciliation across names/units, freshly judged
  under current or recorded headers, scoped miss caching and fair candidate rotation.
- Done: minimized replay expression candidates and reviewed profile-scoped held-out validation helpers;
  ambiguous/complex changes remain investigation evidence instead of enabling a rule.
- Done: GCC failed/successful value comparison via unique unchanged source anchors, omitting ambiguous joins.
- Done: MIPS control-flow/delay-slot and explicit register-dependency region hints in iterative/frontier search.
  Existing fixed regions remain the fallback for other architectures and unsupported transfers.
- Planned: archived build-context reconciliation for imported projects; additional architecture region readers;
  demonstrate held-out retail exact-throughput gains before promoting experimental recipes.

### Saved code audit review

Done for supported ROM audit adapters: local per-project review states and evidence,
image/extent-bound identity, stale-layout rejection, and hidden-by-default confirmed /
dismissed rows. Decisions never pause functions or adopt targets. Indirect transfers
show their register/address; constant-mapping export errors show missing ranges and
sizes. Portable audit-review history, reviewed adoption, additional switch-table idioms,
and proof of callback-table consumers remain planned, alongside the existing non-MIPS
and object-project audit adapter gaps.

Source-preparation context recovery now includes actual small getters and bounded
helper definition order. ROM-mode MIPS entries can retain up to three contiguous,
already-matched predecessor definitions as compiler context after independently
rejudging every body. Matching-only entry extraction prevents duplicate exports;
PROJECT builds continue using their own translation units and build judge. Other
ROM processors will need an equivalent verified-entry exporter before this path
can be enabled for them.
### Bounded indirect-transfer evidence

Done: scalar MIPS ROM audits recognize a guarded absolute word-table pattern, cap
entries at 256 and tables at eight per candidate, reject guard-bypassing edges,
require a unique declared mapping before traversing cases and retain unresolved
paths. Established code mappings can suggest raw-data locations but cannot close
flow without review. Direct-only closure remains distinct from bounded table closure.

Done: bounded straight-line affine load-to-JALR tracing, with separate direct-cell
and indexed/mapping hypotheses; unknown instructions and control-transfer delay
slots break facts. Caps are visible. Reviewed mapping input is accepted through ROM
config `audit_data_mappings` or exported manifest `data_mappings`.

Planned: PIC/relative and 64-bit tables, other guard idioms, inter-block dominance and
alias propagation, runtime callback reachability, overlay-bank-aware mapping identity,
non-MIPS/object audit adapters and explicit reviewed target adoption.

### Evidence-led iterative combinations

- Done: bounded MIPS pairing of measured non-improving local edits, sharing existing iterative phase
  budgets, persistent candidate state, cancellation and the ordinary match judge.
- Done: equal-score measured-output reservation within the existing beam and equivalent-output caps.
- Done: source-free residual diagnosis and explicit pairs/output-diversity ablation switches in frozen
  replay, plus recovered-case and first-exact compile-cost comparisons.
- Planned: independent-target evidence of additional retail recoveries before approving learned
  priority boosts; broader local grammars and other architecture adapters need separate validation.

Callback audit now tracks agreeing scalar MIPS paths, stack spills and field-load evidence. Runtime pointer contents, interprocedural alias analysis and non-MIPS callback dataflow remain planned; no targets are automatically adopted.


### Macro-aware local pairs and discovery measurements

- Done: reuse protected macro regions for bounded local pairs, retaining unchanged directives and
  header dependencies; pass frozen headers through live iterative, replay and exported-ROM search.
- Done: source-free pair-coverage reports and capped process-local generation telemetry, including
  cache hits, wrapped generation time and first eligible proposal; authenticated developer read API.
- Verified: 19 frozen unresolved checkpoints across five compiler profiles, including an 11-case
  held-out other-game subset. No additional retail match or compile saving over main was established.
- Planned: solve the remaining context-sensitive allocation residuals and validate narrower recipes;
  larger budgets and additional independent games before promoting learned priorities.

### Private compiler-unit verification

Done for MIPS ROM entries with explicit ECOFF procedure metadata: bounded real
caller/callee units, independently exact code/data for each known body, and
freshly judged matching-only entry export. Ordinary inline-return placeholders
retain their prior prefix-normalization behavior. Other processor metadata
adapters remain planned; PROJECT mode retains its ordinary whole-project judge.

Saved-progress verification now publishes measured checked/total counters to import jobs and the
setup dialog. Saved ROM progress verification now compiles immutable source/header/compiler snapshots
in a bounded compilation pool (Settings: Background worker threads), with the global compiler-slot
limit still enforced. The pool is separate from shared background jobs that may wait for the project
lock. Normal attempts, byte/data verification, attribution and records remain serial under the existing project lock. Changed
inputs discard a prepared object and compile again; cancellation discards queued work. Already verified
matches and unchanged best attempts retain their existing skip rules. This covers every ROM console
using saved-progress restore/sync; PROJECT mode retains its whole-project build/objdiff judge and does
not use this per-function compilation pipeline.
Local N64 demo benchmark: 32 saved best attempts, two runs each, one worker averaged 8.4 s
and four averaged 2.5 s (automatic repair analysis disabled for measurement). The 32 functions
had identical statuses, scores and saved sources. Actual game import speed depends on compiler and C size.

## Source checkout runtime

Implemented: explicit clean-main Git/uv runtime handoff from Settings, with opt-in automatic main
updates. Git clones and locked dependency/import preflight run in a separate managed runtime while
agents and jobs continue. Current checkouts/environments are never pulled or synced in place. A safe
restart check holds new sessions for at most 15 seconds; if active sessions/finite jobs need longer,
all holds release and the app retries after 60 seconds. Continuous draft/permutation services park
between joined batches. Usage-limit waits are idle and provider cooldowns survive restart. Separate
development data launches without pausing current work. Managed runtime directories are retained
for recovery; automatic pruning and seamless backend replacement remain planned.

Implemented: checkout-only frontend revision polling, in-place CSS updates and debounced interface
reload for scripts/markup/assets. Project/tab, open function, disclosure and main scroll state are
restored; edited text and open dialogs defer page reload. Packaged builds do not watch files.
`DECOMP_LIVE_RELOAD=0` disables it. Backend workers/rooms stay alive; Python changes still restart.

Planned: a desktop shortcut that remembers a selected source runtime independently of the packaged
app, and coordinated idle leases for external MCP clients. Source launches remain manual handoffs.

Hosted rooms in both address and quick-tunnel modes now restore automatically with existing invites
and members, with startup/error feedback. Fixed endpoints preserve client links across restarts;
account-free quick-tunnel hostnames expire when their process exits. A named-tunnel provisioning UI
and managed stable relay discovery remain planned; no stable quick-tunnel hostname is promised.


### Deep searches alongside agent claims

Local deep searches may test the saved candidate while an agent owns the function. ROM candidates
use the ordinary byte/data judge and reject changed compile inputs before saving; PROJECT trials
use the existing tree/unit locks, restore unfinished source and preserve other matched functions.
Only improvements update the best attempt; claims remain owned by the agent until a verified match.
Compiler/private-header preparation waits until the claim is free. One deep search per function
prevents duplicate worker credentials. Room hosts use the local path; room guests still need the
host lease. Concurrent guest proposals without a lease remain planned protocol work.

### Register-cycle lifetime search

- Done: closed MIPS register substitutions are diagnosed from physical linked operands without
  changing scores, data checks or exact-match verdicts. Deep search and iterative repairs share
  bounded lifetime candidates; ordinary project adapters retain their existing exact judges.
- Done: a 24-proposal prioritized deep-search lane, installed native-GCC actual allocation order,
  lreg reference/live-range evidence and architecture-owned GPR aliases. At most two phase
  captures per deep run spend up to four existing compiler slots. Full-input scoped cache reuse
  and compact agent summaries avoid repeated diagnostic builds. Input changes during a capture
  discard its evidence. Transient compiler failures remain retryable.
- Done: a recovered ordered instruction sequence and known matching frame can retain one
  structural alternative within a fixed two-times score ceiling. It never replaces saved C
  unless the ordinary judge measures an improvement.
- Done: conservative relocation of an existing unsigned zero-identity block, with exact scalar
  type checks, definite initialization, unescaped locals, lexical scope and braced-statement
  guards. No arbitrary padding identity is synthesized. A copied near-final compiler regression
  was recovered on candidate 1; earlier checkpoints and three other functions did not recover.
- Planned: register-cycle operand readers beyond the current MIPS notation, non-native/non-GCC
  allocation readers and imported-build phase-capture adapters; held-out game/model evaluations
  before claiming a general token or wall-time reduction. Shared ordinary C repair families and
  the existing imported-project judge remain available without these diagnostic readers.

### Recent capture automation and remaining evidence adapters

- Done: bounded shared-store constants and initialization placement, nested halfword cast
  hypotheses, target-labelled overlay aliases, ABI argument-home and known-callee forwarding
  candidates; shared by local/deep repair search, with ABI candidates also tried by MIPS autopilot.
- Done: captured same-project compiler profiles rank fresh current-header probes; unchanged-C
  context replay separately measures compiler-only and header-only combinations. No header is
  globally adopted from a capture, and fresh byte/objdiff verification remains mandatory.
- Done: repair review separates first matches, source repairs, build reports and context changes;
  function-body diffs remain the default. Automatic analysis queues new captures durably and resumes
  after restart, one bounded analysis at a time; skipped/pending counts are exposed in the dialog.
- Planned: non-MIPS argument-home/frame evidence adapters; conservative complete write-footprint
  summaries for complex escaped SDK buffers; isolated imported-project context replay requires a
  copied project build adapter (standalone captures do not include all build inputs). Ambiguous
  overlay labels need relocation/segment identity and are never guessed from a shared address.

## Transcript-driven agent efficiency

Implemented: cached-baseline batch crash regression, explicit batch execution,
shared connector/account/model configuration rejection, user claim handover,
current-versus-historical baseline messages, shorter default experiment guidance,
and global/per-agent tool presets with task usage and schema character counts.
Normal claim ownership and byte/objdiff judging remain required across both modes.

### Open-file pressure during long-running work

Done: POSIX app startup raises only its own soft descriptor limit to 8,192 when the
existing hard limit permits, preserving higher limits and tolerating denied changes.
Worker SQLite readers close immediately at thread exit instead of waiting for cyclic
GC; cancelled compiler stdout/stderr pipes close even when exception frames are retained.
These shared fixes cover both project modes; Windows retains its native handle limits.
A scratch run of 300 short-lived reader threads under a 256-descriptor limit with GC
disabled completed without errors. The app does not change machine-wide launch limits.

- Done: captured index/lifetime repair candidates shared by local repairs, agent experiments and
  deep permutation: dead unsigned scaling, pointer lifetime endings, indexed-store local reuse,
  constant-trip loops and type-preserving mask staging. Unknown syntax and macro-heavy inputs skip
  these candidates; every result still passes the ordinary mode's judge.
### Timed single-function agents

Done: the function dialog can create one configured CLI/API agent for a named function
and a 1–240 minute wall-clock allowance. Shared claim enforcement pins the function by
stable ID (including batch claims, PROJECT mode and room requests), preserving normal
leases. Busy targets wait before a paid session starts. Context rotations stay on the
same target; matches/set-aside status or expiry stop the worker through normal process
termination and claim release. Deadlines persist across app restart; a manual agent
restart grants a new allowance. The Agents card shows the target and deadline; the
launcher has its own Stop control. No model is started until the user clicks Start.
The function drawer groups AI and local search in matching shared collapsible boxes,
with aligned cost labels and controls in both themes and narrow layouts.


## Focused conversation continuity

Focused workers bypass ordinary 80-tool/160,000-character rotation and receive a continuous-work
prompt until match, user stop or deadline. Codex and Claude persist and resume their exact provider
conversation between turns (never a global latest session), leaving native compaction enabled.
The compatible API loop removes its ordinary step/function cap for focused runs and summarizes
older context in place near its context budget, preserving instructions and the latest complete
tool exchange; summary requests are accounted and recorded. Failed summary requests fall back
to existing context fitting. Paid summarization uses the selected endpoint/model.
Other CLI connectors retain their provider's in-process context management, but exact conversation
resumption after those programs exit remains planned. API history persistence across app restarts
and provider failures also remains planned; saved candidates/experiments still survive.
- Done: project-scoped distinct improved-function totals per room member, alongside matches in the
  host/guest room dialog and room agent cards, including attribution from older attempt histories.

- Done: compiler-suggestion applications run as reusable background jobs with immediate progress,
  matched-function check counts, score-update stages and persistent errors in the UI; ROM ranges
  and source-project file ranges retain their existing regression protection.

- Done: common scaled-store offsets, immediate conditional pointer-load inlining, subscript
  reassociation in owned inline helpers and branch-tail stores are available to shared local
  repairs, agent experiments and deep permutation. Added scoped narrow-index handling and corrected
  a logical-and false positive in pointer escape checks; the normal judges remain unchanged.


ROM compiler suggestion application now rescores affected saved attempts in the existing bounded
judge pool, alongside the already parallel matched-function regression checks. Phase progress
reports checked/total counts and percentages; suggestion cards distinguish default changes
from local address ranges. All affected byte-match checks and atomic stale-input validation
remain mandatory. Broad default changes can still be expensive; narrow range suggestions
avoid rechecking functions whose effective settings do not change.


Naming upgrades preserve descriptive names when a donor proposes a `func_*` placeholder.
Cross-project name actions reject these placeholders server-side and hide their Use name controls;
automatic name inference ignores them. ROM batch/single renames and PROJECT-mode renames also
reject descriptive-to-placeholder downgrades. Source-reuse provenance comments remain intact:
they record donor history and do not themselves change a function's symbol.

- Done: a common “Join an existing room” entry in Decompile together for open projects, including
  hosting, guest, unfinished-project and room-restoration states; reuses the existing verified join
  flow with file selection and progress.

- Done: joining a room from an open project reuses its stored game automatically, with host identity
  verification and no repeated file selection. Source projects use their declared original file or
  an identity-verified program in their configured original directory; missing originals report an
  actionable error rather than selecting unrelated files.

- Done: foreground room joins expose live saved-work transfer counters, current host-checked function,
  confirmed matches and better-attempt submissions through existing job polling. Stage-aware progress
  covers connection, game checking, project setup, host approval, transfer and synchronization without
  presenting a stage's percentage as whole-join completion.

- Done: room match verification offers Retry all for the full pending snapshot, with sequential checks,
  per-item error reporting, counted progress and immediate busy spinners on retry actions.


Single-function compiler testing and the project-wide compiler search now share one progress
template and renderer. The function drawer consumes live job progress for combination count,
percentage, saved/best match, current best profile and exact-match count; project queue/result
actions are hidden in function scope. Navigation guards prevent late results updating another
function or overwriting the project-wide progress card.

- Done: project map first-paint ordering and canvas-cache reset on navigation, with stale-response
  ordering guards and lazy compiler-colour loading. Maps no longer wait for a resize after being
  measured under a hidden loading cover or display cached pixels from the previous project.
Compiler profile groups offer a read-only “Test this profile as default” diagnostic for ROM
projects. It checks saved matching C currently using default settings through the ordinary
linked-byte judge, with checked/passed/failed counts and failure reasons. Other profiles and
unmatched functions are excluded; changed inputs invalidate the conclusion. No settings or
saved scores change. PROJECT-mode per-file compiler compatibility trials remain planned: their
own build configuration needs a separate temporary-build implementation.
- Done: project map first-paint ordering and canvas-cache reset on navigation, with stale-response
  ordering guards and lazy compiler-colour loading. Maps no longer wait for a resize after being
  measured under a hidden loading cover or display cached pixels from the previous project.

ROM compiler profiles can replace the default while retaining previous-default overrides for
saved matches that fail the new profile. The guarded compiler commit rechecks affected matches
and rescores attempts before publishing. A separate read-only trial checks all non-matches'
saved C against a selected profile, reporting exact matches and unavailable sources. PROJECT
mode still needs the temporary own-build implementation described above.

Background work has a global inventory for GUI jobs, managed agents, machine drafts, the
background permuter, autopilot, twin reuse, saved-file verification, compiler surveys and room
services, plus all live app thread names. It delegates Stop to existing safe worker controls and
exposes the shared draft/permuter thread limit. CPU is measured for the Python process only
(100% per core); accurate per-job and compiler-subprocess attribution remains planned work.
Automatic evidence checks and room services remain read-only here and use their project controls.

- Done: explicit ROM export **Verify, commit and push** saves all portable progress and
  verified build changes, commits only sync-owned paths and pushes the configured upstream.
  A missing upstream can publish the current branch to a sole remote and save its tracking
  configuration after success; ambiguous remotes require an explicit upstream.
  Failed pushes retain the commit for retry. Imported-project commit/push UI remains planned;
  those projects already keep their source in their own checkout.

- Done: bound ROM progress checkouts can review and commit app-saved matches, drafts,
  notes, headers and settings without a standalone build. A private Git index captures
  the checkpoint while helpers continue; later edits stay local. Unrelated files, staged
  edits, merge conflicts and concurrent Git operations remain protected. Shared ROM
  progress/Git integration is covered with homemade projects and local bare remotes.

The background-work thread table explains registered thread roles, shows native IDs and sorts
by measured CPU, name or purpose. macOS uses proc_pidinfo(PROC_PIDTHREADID64INFO); Linux
uses /proc/self/task counters. First observations show Measuring; unsupported/failed reads show
Unavailable. Metrics exclude compiler subprocesses and do not attribute shared pool work to
individual jobs. Windows per-thread counters remain planned work.

Background activity and thread details now share one CPU-sortable table. GUI jobs record their
native thread ID; agents use their worker thread, and unique project-controller/room thread
names are associated where reliable. Associated threads appear once on their work row; shared,
unknown and ambiguous threads remain separate read-only rows. CPU totals cover listed threads
only, with no guessed attribution of shared pools or compiler subprocesses.

Completed: contributor statistics and function-map recency share revision-invalidated project history
summaries, backed by a covering measurement index. Live updates reuse cached room statistics. Slow
UI/database diagnostics identify the route/caller without recording private inputs.


Completed: live agent metrics are scoped before querying, use narrow measurement-only history,
refresh on project attempt revisions, and share concurrent refreshes. Helper-session billing lookups
have a worker-specific index. Global agent views and reset/lifetime accounting remain available.


Completed: the function drawer defers optional cross-project and SDK name similarity work to the
existing peers disclosure request, for both ROM and imported projects.

- Done: local ROM deep searches offer 1–16 compiler profiles per C candidate, default first then
  distinct profiles ranked by matched-function usage. Tests and exact-bundle verification share the
  compile allowance; misses are cached per profile. Project-wide passes remember the count and
  invalidate completed scopes when it changes. Imported-build and host-owned room profile search
  needs separate build/host support and remains planned.



Completed: deep-pass polling reuses revision-scoped queue/error checks; ROM maps cache cross-project
byte relationships; compiler display reads use covering metadata and separate automatic maintenance;
profile hypothesis extraction is compact and cached using the existing project/example index.
Optional startup console/SDK work is staggered and yields briefly to navigation. Scope invalidation,
external imported-project expiry, explicit checks and the ordinary byte judge are preserved.

- Done: batch deep-pass queue reads, bound inspector chart/note payloads, reuse function
  records, coalesce worker snapshots, move response encoding to workers with bounded
  full-map wire caching, and add opt-in request/SQL/browser timing diagnostics.

Completed: transcript-driven agent workflow improvements: structured empty-queue waiting,
shared scoped saved-C diagnostics, normalized assembly focus and inline mismatch windows,
broader unverified correctness-report capture with explicit alternative references, and
bounded asynchronous CPU handoffs with cancellation and existing repair-trial history.

- Done: batched compiler-comparison eligibility preparation with live checked/total/percentage progress,
  shared input encoding and preserved completed-comparison fingerprints. Saved best C avoids a needless
  latest-attempt lookup.
- Done: incremental agent card measurement counters with correction/reset invalidation, batched polishing
  totals, compact live sections and deferred hidden map refreshes, persistent revision-checked ROM fingerprints
  and cross-project map identities, narrow SDK naming reads with bounded unchanged-check reuse, and scoped
  autosave project/contributor reads. External progress-file checks and byte verification are preserved.

- Done: function drawers paint saved code and assembly from a lightweight preview on the small-request queue,
  then load history, drafts, calls, verification and compiler details with visible progress and retry. Both ROM
  and imported projects retain full analysis; late detailed responses preserve edits and navigation. Baseline
  evidence lookups use a function/score index and no longer materialize entire historical attempts.

- Done: pipeline deep-pass preparation and execution with shuffled lightweight identities, bounded queued turns,
  live candidate-scan progress, shared header/symbol/declaration preparation, skipped unnecessary historical
  fingerprints and fresh dispatch validation.

- Done: correction-aware incremental history and agent counters, copy-on-write summary records, incremental
  map row reads with structural fallbacks, score-independent boundary/SDK caches, indexed worker handoffs,
  covering history metadata reads and separate database connection/lock/execute/fetch diagnostics.

- Done: indexed metadata-first cross-project suggestion ranking, narrow and single-flight ROM fingerprint
  invalidation, transactionally maintained compact profile evidence, coalesced/batched room members, shared
  worker scope inputs and an LRU read cache that protects active builds from eviction.

- Done: the KMC-compatible legacy MIPS GCC pack provides a fixed, no-argument debugger
  BREAK 9 compatibility intrinsic. Submitted assembly remains rejected; ordinary C
  without the intrinsic compiles unchanged. Other MIPS compiler packs still need
  compatible, tested registrations; compiler dialects and existing intrinsic headers differ.

### Return-contract and narrow-expression recovery

- Done: bounded target-backed return candidates, caller-backed private m2c context,
  pure narrow-expression inlining with conversions preserved, and gated plain local
  float slots. Shared local/deep search composes them and redirects repeated identical
  output while retaining exploration; the exact judges remain unchanged.
- Planned: return/delay-store evidence readers beyond MIPS and multi-exit return
  analysis; imported target-text/caller-graph adapters where unavailable. Shared pure
  expression repair already reaches supported 32-bit ROM and imported projects.

### Conversion scheduling and missing hardware operations

- Done: shared local conversion scheduling candidates (multiline debug conversion
  tails, delayed narrow captures, known-zero-bit shift masks), explicit m2c BREAK 9
  repair and missing-operation frontier guidance. They reach ROM and object-project
  callers of the shared repair generator; debug-tail targeting currently requires
  MIPS `mfc1` evidence and GNU debug flags. Other processors need tested hazard/line
  diagnostics before that family can be enabled for them. Fixed debugger-break
  compiler support remains limited to the registered KMC pack.

### Measured hazard and scalar capture recovery

- Done: bounded MIPS branch/NOP line-boundary hints, range-proven low-zero masks, and
  explicit post-call narrow captures shared with local/deep search and iterative
  autopilot. Frozen private precursor replay verifies rediscovery with unchanged judges.
- Planned: hazard evidence readers beyond MIPS; imported builds without measured
  function rows cannot use that hint. Scalar families reach supported 32-bit ABIs.

### Fixed live MIPS return-address snapshots

- Done: callback-backed live return-address repairs also recognize an existing
  entry-caller builtin workaround. A bounded one-shot statement companion is judged
  atomically, retaining source lines and hardware-family priority; restored entry
  registers, no-call targets and unsupported destination widths are excluded.

- Done: a no-argument current live-link-register intrinsic in the registered KMC
  compiler template, used-template portable export selection, raw absolute-store
  evidence and a shared local repair for single-use uninitialized placeholders.
  Real compiler tests distinguish current reads after calls from saved snapshots.
- Planned: tested registrations for other compiler packs and explicit real-name
  symbol adapters for this hardware-store repair. Other processor link-register
  snapshots require their own semantics/evidence; no cross-processor guesses.

- Done: target-backed live return-address repairs can combine a fixed hardware
  read with bounded typed literal inlining or declaration staging. Shared draft
  repair consumers use the family on MIPS; other processors have no corresponding
  proven hardware reader. Ordinary linked-byte or objdiff judging still applies.

- Done: opt-in automatic exact-bundle recovery also rechecks saved deep-permuter
  source/profile pairs when the original search lacked promotion allowance. Each
  pass attempts one fresh verification; failed newer evidence no longer hides
  older untried exact pairs. Claims, input freshness and ordinary byte/data gates
  remain required; imported project builds retain their existing exclusion.

- Done: applicable target-backed breakpoint/live-register proposals retain priority
  through learned ordering and the final deep queue, ahead of generic rewrites.
  Alternate-profile exact matches update best score/percentage and improvement
  counts and discard the stale previous-profile diff.

- Done: move-on guidance includes the current release claim token, avoiding rejected first handoffs after a same-worker reclaim while preserving stale-lease guards.
- Done: shared deterministic repairs can separate a proven constant global
  pointer field argument from the pointer's later capture. The bounded proposal
  preserves helper purity, pointer types and evaluation constraints, reaches ROM
  and project repair consumers, and reproduced a private MIPS GCC linked match at
  rank 6 of 34. It leaves callee prototypes to verified source context.
- Done: independent patch batches identify all invalid variant anchors and their current revision before any trial compiles, allowing one precise correction without partial edits.

- Done: inferred candidate-local records recognize repeated aligned scalar accesses
  through initialized local byte pointers, with no other pointer uses. Shared repair
  paths retain the existing field/access bounds and ordinary judge; guards reject
  mutation, escape, shadows, unknown uses, qualifiers and preprocessor directives.

- Done: MIPS ROM target diagnostics recognize constant `0xffff`-masked low addends
  when an adjacent annotated high and raw register/address pair prove the full address.
  Older target metadata receives the same completion in the display object, preserving
  stored bytes, relocation records and ordinary linked verification. This shared MIPS
  path reaches ROM consoles; object-project judging already reads object relocations.

- Done: local repair discovery reuses its revision fingerprint across one proposal pass while retaining fresh tried-target lookups and recomputing scope on the next pass.

- Done: bounded scalar normalization scopes preserve a saved signed value while
  ending its copied-from lifetime inside one expression replacement. Shared
  draft/agent/permuter candidates reuse an existing unused scalar, preserve opaque
  unrelated macros, reject observable or out-of-scope locals, and retain the
  ordinary compiler and byte/object judge as the acceptance gate.

- Done: shared draft and agent repair searches can propose one unambiguous copied
  callee-name correction from MIPS symbolic direct calls, preserving source-owned
  prototype types and the ordinary exact judge. Other processor call-syntax adapters
  remain planned; ambiguous multi-callee remapping is intentionally unsupported.

- Done: read-only address resolution recognizes both imported `func` and legacy
  `function` symbol kinds while preserving data conflicts and overlay ambiguity.

- Done: compact batch claim queues retain each queued function's release token,
  with cheap claim-metadata recovery guidance and unchanged stale-lease protection.

### Persistent local match library

- Done: separate private SQLite library retains verified source, context and compiler variants,
  normalized function fingerprints and per-game occurrences after projects are closed/removed.
  Identical source/context blobs are shared. ROM and imported destinations retain their normal
  byte/objdiff judge; reopening inputs never turns a fingerprint into automatic acceptance.
- Done: archive scans batch saved evidence, use direct symbol lookups and reuse shared header
  analysis/text hashes. Persistent ROM input stamps survive unrelated app updates; completed
  batches remain available after Stop.
- Imported candidates whose dependencies exist only in external project headers still need those
  declarations in the destination build, as with the existing cross-project transfer adapter.

- Done: the shared native randomizer wrapper excludes artificial self-assignment
  insertion in traced and untraced jobs, preventing that family from introducing
  unchecked signed/pointer increment-decrement cancellations or self-reads.
  Historical candidates still require review; ordinary judging is unchanged.


- Done: shared draft, autopilot, agent and deep repair searches propose at most two
  discarded unsigned-byte increment spellings from a direct byte read or one unique
  dominating cached-byte load. A bounded CFG checks every path for intervening effects;
  exact byte addresses and ordinary declarations are required. Unknown qualifiers,
  aliases, macros, shadows, cycles and used assignment values skip the family.
  Compiler and ordinary byte/object judging remain unchanged across supported processors.

### Completed: bounded loop/source/profile preflight

Normal and deep ROM permuter runs share a small paired return-guard/jump-threading
search for supported old MIPS GCC bounds loops, with branch-delay diagnostics and
normal exact bundle promotion. Imported projects own their compiler settings and
do not receive this ROM-only profile probe; other architectures/compiler families
need independent evidence before receiving this specific GNU C repair.

### Compiler-mapped local storage follow-ups

- Done: bounded same-frame spill-pair and single-register-chain hypotheses, bound to
  current compiler C-line mappings; shared local experiments, CPU follow-ups, iterative
  autopilot and deep refinement retain the normal exact judges. Broad line-family
  exploration is interleaved when current rows lack scheduling/control evidence.
- Planned: residual readers beyond MIPS and source-map adapters for unsupported
  imported/debug-object formats. Missing or stale maps cannot supply targeted edits.
- Measured limit: the 22-case copied held-out sweep produced no new matches or score
  improvements. Sixteen fewer broad line trials did not reduce total trial count.

- Done: shared draft, agent and deep repair discovery includes bounded pure integer
  byte-address inline helpers, conservative if/else expression-arm scopes and adjacent
  call-line bridges. It preserves types/evaluation order, rejects opaque preprocessing
  and qualifiers, rotates bridges across accessor sites, and relies on ordinary exact
  judging; host semantic and pinned period-compiler regressions cover the supported C.

### Completed: measured-output repair diversity

- Bounded local and deep search now pivot toward storage/lifetime/control-flow families
  after six inert outputs across at least three families. Measured distinct outputs
  influence ordering; every fourth selection reserves exploration for an untried
  family. Initial sibling probes and existing deferred exploration remain available.
- Up to four shared-budget combinations (two per parent) can test individually inert,
  disjoint small edits. Unknown/cached outputs do not provide interaction evidence;
  literals, preprocessing protections and ordinary exact judges remain unchanged.
- A repeated plain local integer-array scan can stage its value in a fresh scalar or
  a CFG-dead existing temporary. Short-circuit guards stay ahead of the load; volatile,
  pointer, side-effecting and unsupported branch shapes are not rewritten.
- Validation uses private frozen failed/successful compiler-output controls and held-out
  cases, without adopting game matches or claiming measured AI-token savings.
- Measured result: at the same 24-candidate limit, a private controller checkpoint
  improved from score 330 to 225 (previous policy stayed at 330), without becoming
  exact. Five frozen held-out failures recovered 3/5 successful controls with either
  policy, using 51 candidate compiles per policy. This is evidence for the supported
  staging repair, not a demonstrated general match-rate or token-cost improvement.

### Completed: progress-aware repair selection

- Search distinguishes distinct outputs from useful score or bounded regional
  improvements. A changed-but-worse output no longer clears the separate progress
  stall counter; six nonproductive trials across three families can pivot strategy.
  Initial sibling probes and untried/deferred exploration remain available.
- Supported MIPS scan temporary reuse uses current compiler-mapped residual lines
  and CFG liveness to rank donors, retaining the original probes. It never infers a
  physical register binding from a source line or changes exact judging.
- Offline replay supports preselected harder cohorts by captured failed score,
  instruction count and distinct target/build scope. It freezes input hashes before
  compiling and reports selection metadata for policy comparisons.
- Private fixed-budget validation: eight frozen harder captures (24 candidate trials
  each maximum) recovered 2/8 controls with either policy. Candidate compiles fell
  from 164 to 160; regressed trials fell from 78 to 53, while compile failures rose
  from 2 to 5. Best scores were unchanged across the cohort. Individual recoveries
  moved from 6 to 9 trials and from 14 to 7 trials. This small comparison does not
  establish general match-rate or model-token savings; no live matches were adopted.


- Done: shared `pointer_capture_reuse` draft, agent-local and deep repair family reuses dead typed
  pointer captures without moving evaluations, with bounded independent pairs before singles.
  Exact type/constant array bounds, CFG liveness and unique sink paths exclude unknown aliases,
  qualifiers, escapes, macro effects and incoming jumps; ordinary verification remains authoritative.

- Done: importing unchanged note metadata during a source edit preserves the existing note
  rows instead of deleting and re-attributing the complete history. Explicit manifest note
  edits still replace history; source edits retain notes added concurrently after the render.

- Done: unchanged ROM rematch checks reuse the original repair example and review instead of
  enqueuing repeated automatic analyses. Different source, target, build context, failed
  baseline or attribution remains separate; every judging attempt is still retained.
- Planned: imported-project repair capture deduplication needs complete per-file build input
  snapshots; keep their captures separate while compiler/header evidence is incomplete.

- Done: exact C header history shares SHA-256-addressed zlib content across versions and
  projects, with atomic legacy migration and bounded decoded-content caching. All console
  and project-mode callers retain the historical SQL read/write interface.
- Done: Settings previews and removes only unreferenced C header versions, protecting
  current, attempt/repair, recovery and in-flight-read history; a separate cancellable
  manual SQLite compaction task reclaims physical space without changing exact judging.

- Done: C/assembly compiler scratch follows explicit app data storage (`DECOMP_HOME`), with
  `DECOMP_COMPILER_TMPDIR` override and standalone OS-temp fallback. Host disk/quota/file
  exhaustion remains a retryable resource failure, with accurate progress diagnostics
  instead of a misleading git/permissions message or failed candidate attempt.

### Completed: investigated repair-generator failures and structural priorities

- Argument-removal generation checks one direct fixed-arity contract despite
  unrelated macros; ambiguous preprocessing, prototypes and types remain opaque.
  Narrow-store temporaries use a compound C89 statement and preserve `if/else` arms.
- Applicable array/pointer phase, inferred-record and scalar-normalization families
  receive earlier initial ranking and round-robin draft slots. Compiler refinement,
  guarded probes, measured deferral and ordinary exploration remain available.
- Six unresolved private captures received bounded edit ablation. The supported
  zero-subtraction spelling now reaches the existing normalization scope repair;
  coupled qualifier/prototype/store-order changes remain investigation evidence.
- Investigated-cohort replay (eight frozen captures, at most 24 candidate trials each):
  control recoveries increased from 2/8 to 3/8, candidate compiles fell from 160 to
  132 and compile failures fell from five to zero. The record-layout recovery took
  one trial instead of seven; the new normalization recovery took two. These cases
  informed the changes, so this is a regression result, not held-out generalization
  or a measurement of AI-token savings. No live game progress was adopted.


### Completed: selected empty-callee and incoming-copy repair

- Shared bounded `matched_zero_argument_call`, `incoming_parameter_alias` and
  `zero_argument_parameter_alias` proposals reach drafts, agent local variants,
  autopilot and deep/permuter local passes in ROM and PROJECT modes. Selected
  matched explicit empty `void(void)` C is required; source-owned declarations
  only, no callee or shared-header edits.
- Pure scalar arguments, compatible unchanged formal copies and dominating
  assignments are checked. Macros, escapes, shadowing, mutations and uncertain
  definitions block proposals. Pinned homemade compiler controls and a copied
  cartridge check confirm the measured composition. Complex expressions and
  shared interface migrations remain manual investigations.

- Done: bounded `promoted_entry_local` proposals preserve signed-int interfaces
  while representing a stable entry narrowing as a real byte/short local. Shared
  agent variants, local/deep permuters, iterative repair, donor follow-ups and
  offline replay/benchmarks receive explicit C32 eligibility from direct pinned
  GCC profiles. Unknown flags/commands, C++, conditional type contexts and
  macro-expanded recursion stay excluded. Two private decoder proposals reproduce
  score 1100 → 705 (not a new match); homemade native boundary controls verify the
  integer conversion behavior.
- Planned gap — promoted entry locals in external PROJECT builds and compiler
  adapters: their build commands may compile nominal `.c` as C++ or change integer
  widths. Do not enable this family from a filename. Supply complete current
  invocation language and target-width evidence before enabling it there; existing
  repair families remain available.

- Done: bounded unsigned shift/add staging through shared local repair generation,
  including combined sites, unchanged compiler/link judging and C32 invocation
  guards. A real copied N64 draft improves 322 to 172, not exact. External PROJECT
  compiler commands without proven C32/macro scope remain excluded, as with
  promoted-entry and pure-preheader repairs; invocation evidence is planned work.

- Done: automatic SDK naming can use unique full-size assembly identities through
  the normal guarded rename paths. Assembly classification and C queue exclusions
  are preserved; ambiguous, small, disabled-library and colliding names stay out.
  A read-only real indexed project finds four exact identities among34 assembly
  routines (three still generated names). This is naming evidence, not matching C.

- Done: bounded `component_index_capture` through shared agent/local/deep/iterative
  repair generation, with C32 invocation guards, expression-macro checks and CFG
  initialization dominance. A copied N64 draft improves357 to277; combining the
  existing debug-boundary repair gives177, not exact. External PROJECT builds still
  require complete invocation evidence before enabling this guarded family.


- Shared local zero-chain splitting now proposes two real independent stores for
  matching visible unescaped32-bit integer locals, with proven direct C32 scope.
  Copied V-Rally replay705->695 is not exact. External PROJECT commands without
  invocation-scope evidence remain excluded, as for promoted-entry local repairs;
  collect trusted language/int-width evidence before enabling this family there.


- Shared ROM/PROJECT function briefs now flag bounded explicit callback-formal
  discrepancies against selected matched definitions, without extra database
  lookups or source changes. Typedef callback types, header-only callback evidence,
  indirect calls and macro registries still need trustworthy type/dispatch evidence;
  this advisory check must not be described as a complete registry auditor.

- Compact ROM/PROJECT claim receipts and candidate reads now expose bounded
  pending quality concerns tied to the exact returned source revision. The cue
  reads only the saved report, without hydrating alternatives or the full brief;
  reviewed/historical reports do not attach to a different candidate. Reports
  remain hypotheses and never block work or automatically select source.

- Shared random and deep search reject newly introduced empty literal-zero do
  loops before judging them. Existing empty wrappers, genuine nonempty blocks
  and dynamic-condition waits remain available. This prevents alignment-only
  scaffolding from being retained as an apparent improvement; it is a bounded
  source-shape guard, not a C equivalence checker or ban on block rewrites.

- Shared local/deep repairs can sink identical plain-local product tails from
  two flat braced branches, including placement after one independent literal
  assignment. Both original copies are removed; unsafe flow/storage shapes are
  left unchanged. A private copied unmatched witness improved2238 to2043, not exact.

- Shared ROM/PROJECT local and deep repairs can normalize an actual packed-byte
  decode before widening into existing scalar carriers. The bounded family needs a
  known32-bit C profile (C++ reference calls need a different proof) and uses
  a visible byte load, rejects unsafe/unknown bindings and introduces no storage,
  reads or assignments. A private unmatched witness improved1848 to1733, not exact.

- Done: applying verified compiler-profile matches no longer skips claimed ROM functions.
  Exact-only rechecks retain input validation, release the displaced claim only on commit,
  and rotate its managed matching worker onto eligible work. CLI conversations are ended;
  API conversations switch at the next response/tool boundary without replaying obsolete calls.

- Done: the shared native-permuter initialization guard also checks newly introduced
  pointers to known integer/void types. A pointer assigned only inside a conditional
  cannot be treated as initialized after that conditional; a pointee write does not
  initialize the pointer. Ambiguous bindings still receive no verdict.


- Done: native initialization checks retain independent verdicts for introduced
  integer/pointer bindings and look through inert local scalar-record typedefs.
  Unsupported writes to one binding no longer hide a separate uninitialized read;
  complex declarations and unsupported semantics still receive no verdict.


- Done: shared ROM/PROJECT native input preparation expands supported visible
  two-argument byte-field accessors, including nested reads and assignment lvalues.
  Ambiguous preprocessing and unsupported templates stay opaque; this is not a
  replacement C preprocessor or an automatic match guarantee.

- Done: `guarded_call_value` offers bounded equality-proven local values in direct
  call arguments through the shared deterministic pipeline and agent variants.
  ROM and imported projects share the supported 32-bit C proof; unknown ABIs and
  ambiguous source remain untouched. A copied prototype-correction replay recovered
  exact bytes, without counting an additional game match.

- Done: Codex CLI sessions explicitly enable and require the app MCP transport with a
  bounded startup timeout, including focused resumes. Required-transport startup failures
  show redacted CLI diagnostics and use connection backoff before a model turn starts.

- Done: registered GNU MIPS fixed FCSR exchange intrinsic and bounded machine
  leaf seed. ROM/project machine drafting shares the source generator; imported
  projects must expose encoded assembly to that generator. Exact prepared source
  approval remains required for retained assembly; no automatic reclassification.

- Done: registered GNU MIPS fixed CP0 Count read and bounded complete-leaf seed.
  Shared ROM/project machine drafting uses the same generator where encoded
  assembly is available. Retained assembly still requires explicit normal
  verification and approval; the seed does not change its classification.

- Done: registered GNU MIPS fixed CP0 Status read with a bounded complete-leaf
  seed, ordinary/diagnostic regression checks and no automatic assembly reopening.
  Other backends and arbitrary CP0/control-register hazard sequences remain unsupported.
- Done: fixed registered GNU MIPS CP0 Compare write and bounded complete-leaf
  machine seed. Ordinary linked judging remains unchanged; handwritten provenance
  and timer-interrupt clearing semantics remain explicit.
- Done: KMC MIPS complete-leaf drafting for a fixed Status write with one
  return-branch spacing instruction, grounded in the VR4300 ITS immediate-branch
  stall warning. This does not provide full Status hazard completion, arbitrary
  inlined barriers or automatic reopening of assembly. Other backend assembler
  state policies remain unvalidated; the automatic seed is KMC-only.


- Done: caller-backed MIPS loaded-word return evidence reaches ROM machine-draft
  context and advisory hints. A load alone never enables return rewrite candidates.
- Planned: expose equivalent direct-caller consumption evidence for imported
  projects where no caller graph is currently available. Keep inference disabled
  there until such evidence exists; do not infer a return type from a scratch load.

- Done: shared, checksummed compression for large function text, reuse feedback,
  compiler evidence, transcripts, compiler settings and progress manifests. Bounded
  legacy backfill, match-library compression and two-file compaction preserve exact history.

Implemented: bounded MIPS conditional tail-dispatch advice in agent contract briefs,
including assembly review without saved C, for ROM and project modes when raw target
bytes/address are available. It asks for caller/callee ABI and compiler sibling-call
capability before variants, without changing signatures, profiles or classification.
Other processors have no reader for this instruction pattern; generalized tail-dispatch
ABI analysis and automatic source/profile selection remain planned.

- Done: repair evidence samples repeated source transformations by compiler/header context;
  installed automatic rules keep one regression example and manual repairs keep three,
  with durable repeat counts and no repeated analysis scheduling. Existing evidence remains
  available; conservative capture applies when inputs cannot be grouped safely.
