# Agent workflow efficiency

These changes address repeated patterns in recorded app exchanges. They do not modify the
exact-byte ROM judge, imported-project objdiff verdicts, or turn agent reports into proofs.
The transcript sample predates these changes; it is not a performance benchmark of this build.

## Empty queues

`claim_function` returns a compact `Work availability` JSON receipt when no eligible work is
available and the worker holds no live claim. It includes the work revision, a reason, a
30-second retry interval and counts. `open` includes held functions; these are not disjoint
categories. The app can finish the current conversation at a completed-tool boundary and
wait without another model request. CLI and API startup also stop before starting a provider
request if their preloaded claim finds no work.

Receipts are scoped to the worker/project, build inputs, selection, live claims, pause/grant
records and repair implementation. They expire after 30 seconds. Explicit named claims and
focused workers bypass the receipt cache. A worker holding work is never stopped by this
mechanism. The existing manager eligibility checks remain authoritative.

## Saved and exploratory scores

Successful saved-C diagnostic baselines are shared across workers through the existing
bounded single-flight read cache. Keys include build inputs, the saved-source hash and scoring
revision. Imported build dependencies are hashed too. Shared entries expire after ten seconds
for imported projects; ROM entries last while their input scope and bounded cache entry remain valid. Input changes and source-only edits
invalidate them; transactional callers bypass sharing. Failed probes are not shared verdicts.

Feedback distinguishes the historical saved score, the saved C rechecked with current inputs,
and the exploratory candidate. Older recorded settings remain unavailable when they were never
captured; a fresh saved-C recheck supplies the current comparison instead. A diagnostic never
changes the historical best, records a match, or promotes cached results. Subsequent submissions
still use the ordinary judge. Feedback detects stale tagged outcomes after build changes.

## Mismatch feedback

The existing mismatch summary already normalized proven relocation-rendering differences.
Assembly-focus selection now uses that same view, including an adjacent target anchor for
candidate-only insertions. Compile feedback includes up to five normalized instruction rows
around the first real mismatch, with bounded line lengths. Raw diff and full assembly remain
available through paging; scores, raw rows and byte verdicts remain unchanged.

## Correctness concerns

Release-note capture recognizes phrases such as `wrong C`, `wrong logic` and missing counter
resets. These remain agent hypotheses tied to the current saved revision. An explicit
`Start from <revision>` or corrected-version reference can link a different compiled, scored
alternative from that function's recent history. No alternative is selected merely because
it has a higher score, and no C is automatically labelled correct. Existing structured issue
reporting and user review remain available.

## CPU handoff

After an agent releases compiled work without a dependency or assembly verdict, the existing
deep-permuter pipeline runs as a background job, including its shared local-repair proposal
pools and ordinary judging. It uses the same six-compile,
eight-second allowance as the previous synchronous release repair. The time limit is soft:
generation or one in-flight compile may cross it. No model calls are made.

The app allows one handoff job per function, eight queued/running handoffs and one active CPU
handoff slot. It reloads inputs at dispatch and skips changed, removed or matched functions.
The repair lane acquires its ordinary lease before compiling and disables parallel side searches. A running deep pass owns the
work instead of receiving a duplicate job. Room guests retain host ownership and skip local
handoffs. Jobs publish progress and can be cancelled; shutdown cancels queued/running handoffs.
An update can defer new handoffs without undoing the release. Interrupted queues themselves
are not persisted; saved improvements and ordinary repair trial history remain available to
the next deep pass. A bounded handoff never claims all possible repairs were exhausted.

## Verification

`tests/test_agent_workflow_efficiency.py` covers idle receipts, held work, explicit claims,
pause grants, skipping provider startup, concurrent baseline sharing, source/target changes,
failed probes, stale score comparisons, normalized focus/insertion windows, quality references,
and asynchronous CPU handoff deduplication, input checks and cancellation. All fixtures are
synthetic and use scratch state; no new real-game matches are claimed.

## Transcript review follow-up

Settings → Agent blocker review lists repeated concern reports without compilation. New reports
are scoped to source, header, compiler/build inputs, symbols, callees, target bytes, instructions
and repair implementation hashes. Changed fields are named in the review and the next claim brief.
Three unchanged concern releases without recorded compilation request review. Completed explicit compiler diagnostics count as work even when they create no scored attempt; their per-function timestamps are compared with the claim start. This does not suppress explicit claims, change
eligibility rules, label assembly, or change a match. Acknowledge records that the user reviewed
the current evidence; it does not grant a retry or resume workers.

Earlier notes explicitly reporting no compilation can also appear after three concern reports.
Their historical build scope is unrecorded and shown as such: they are not assertions about the
current candidate. The list is bounded to 200 functions and twelve recent releases/notes per item.

Agents can include `next_experiment` in `release_function`: `revision`, `old`, `new` and an optional
`title`. The revision must match and the old anchor must appear exactly once; unchanged, stale,
ambiguous or oversized proposals are rejected. This stores an untested proposal, without compiling
or changing source. `get_function(section="experiment")` rechecks source and build fingerprints
and supplies current `compile_patch` arguments only while they still fit. The ordinary judge
remains authoritative. Natural-language notes are never automatically converted into edits.

Claim packets retain complete editable C and user instructions. Auxiliary line evidence is bounded
to 900 characters and experiment summaries to 800, with supporting reads on demand. The failed-family
summary and retry context precede the candidate so agents can avoid known ineffective work.

Provider-account cooldowns already persist across restarts and are credential/endpoint scoped for
API workers. CLI limit detection now publishes the wait immediately before session exit handling,
so sibling workers see it before starting another request. The generic exit-code error is not also
emitted for the same quota stop. Already-running workers are not killed.

## Data-region review and independent verification

`report_data_region(function, start, end, reason)` reports a whole suspected data table, with
an exclusive end address. The representative function must be claimed. Its unit and original
image position identify the overlay; equal RAM addresses in other images are unaffected.
Repeated identical reports share one card in Settings → Data region review. The user may
pause new claims, resume them or dismiss the report. Held claims may finish, saved matches
stay matched, and no automatic data/assembly verdict is made. Changed target bytes or
boundaries lift the pause and require fresh evidence. This metadata workflow shares the
ordinary claim path across ROM and imported projects; entries without a known address cannot
form a region. Region changes invalidate cached idle receipts.

Successful structured boundary, data-region and candidate-concern reports count as investigation
outcomes, separately from compilation. An ordinary note, release, failed report or model assertion
does not count. Investigation sessions do not receive the no-compilation failure or automatic
compilation nudge. Recorded matches still require the ordinary byte judge.

Knowledge prompts omit pending proposals already supported by that model. “Verify with another
model” queues the proposal and its cited examples for an existing active, non-focused helper of
a different model, at its next session. It does not create another paid agent, interrupt a current
conversation, or accept the proposal. With no eligible helper, the UI explains how to start one.
Independent reproduction is still required before backing; manual acceptance remains available.

## Prepared compiler evidence and CLI startup

Prepared bundles watch their function's effective compiler profile rather than unrelated ranges,
per-function selectors or boundary/knowledge review metadata. Other configuration and all symbols
remain conservative dependencies. Changed-input errors identify components without exposing source
or symbol values. A symbol/build-file race may trigger one additional complete compile and linked
byte/data verification while the same claim is valid. A changed saved source, header, target,
profile or expired claim needs a fresh explicit proposal; no retry bypasses the final atomic guard.

CLI startup has two checks: the app-only authenticated server preflight, then an actual
`project_info(detail=false)` call by the spawned CLI. No new function brief is handed over before
the second check; mutation and claim tools reject calls until it succeeds. Failed handshakes
reconnect once, then pause. That retry requires another model request, unlike a server preflight
failure. Provider limits, sign-in failures and explicit configuration errors retain their own
handling. A completed handshake is recorded through the existing tool transcript. Existing focused
claims and conversations are retained; the resumed conversation confirms access before continuing.
Room clients send region evidence to their host, whose project controls claims; a client cannot impose a
local pause on the host's queue. The startup availability check also excludes other agents' live claims,
so an entirely busy queue retains the existing wait-without-model-request behavior.

## Address ownership, model IDs and release receipts

`resolve_addresses(queries=[...], context_function="...")` resolves up to 20 symbols or hexadecimal
addresses in one read-only call. Results distinguish registered function entries, interiors with offsets,
registered data, ambiguous overlays and unknown addresses. A unique held original image supplies default
context; an explicit function or `function+offset` keeps its own image. Existing imported-project symbol,
unit and C++ name lookup remains available. Missing address metadata stays unknown; registered extents
are analysis evidence, not a proof that bytes are executable code.

`get_function` and `get_assembly` also accept an unambiguous address or generated data-style alias and
identify the canonical function used for the read. Registered data is not silently converted into code.
Mutation tools retain ordinary canonical-name resolution and lease checks; this does not rename symbols,
change boundaries or weaken matching. Both ROM and imported projects use the shared metadata catalog.

The launcher validates the connector's model ID before preparing a project or creating helpers. Opencode
requires a full provider/model ID. A short model name can resolve only to one exact leaf in already cached
discovery; ambiguity or missing discovery asks the user to select/refresh full IDs. Explicit qualified custom
IDs remain supported. An explicit ProviderModelNotFoundError is a configuration failure, including when it
appears only on stderr: it pauses automatic retries instead of spending the tool-handshake retry. Discovery
is not a paid inference, and launch validation does not run another discovery command.

Completed agent releases save bounded receipts (two replies per agent/function/operation, at most 4096
characters each). Repeated releases reuse the recorded result, without adding notes, abandon counts,
assembly votes or dependency pivots. Push-back guidance is not a completed release. A claim_token in briefs,
get_candidate and continuation responses identifies the lease using its timestamp and recorded claim event,
so consecutive claims remain distinct even when the clock is identical. An old token cannot act on a new
claim. After reclaiming a previously released function, an unversioned tool request either replays its old
result while preserving the new claim, or asks for the current token. Machine releases remain explicit
current-claim operations. Room clients retain and forward the host's lease token, and host transport releases
use the same retry guard. Older room clients without tokens may need an update when releasing a reclaimed
function. An abbreviated historical reply explicitly asks the agent to retrieve current state before acting.

## Candidate-change explanations

Stale-revision errors, get_candidate and compilation continuation responses identify recorded source
provenance: agent, attempt ID, compilation route and measured score reduction when available. Batch selection
and recovery-base selection are described separately from the author of the compiled source. Trial family and
variant are shown only from matching recorded provenance; inherited/mismatched source evidence is not
attributed as a new rewrite. Unrecorded file/draft changes explicitly have no inferred writer. Historical scores
remain historical and every new match still goes through the ordinary judge. These views do not compile,
select another source, or change a claim.


## Responsive connections and claim recovery

Compact `project_info` reads saved configuration and indexed project totals. Full overview, SDK/room facts,
knowledge and compiler suggestions are deferred to `detail=true`; explicit project instructions are retained.
MCP initialization, authenticated tool listing, authentication and transcript persistence run in workers,
not on the HTTP event loop. Metadata and app-only health checks have dedicated capacity independent of
ordinary tool workers. At most two app startup probes run concurrently; busy probes retry without a model
request, using staggered backoff. Per-tool transcript responses include numeric queue, dispatch, SQL-read,
build and pre-dispatch durations. Build duration includes compiler/build-lock waiting; SQL timing covers
Store reads, not all transaction writes. Timings overlap and are not CPU measurements.

Agent `claim_function` reserves ordinary leases and returns saved candidate C and a claim token without
running diagnostic builds, drafting or imported-tree inspection. Same-agent/project claim requests serialize;
unchanged retries return the existing receipt. Supporting assembly, notes and drafts remain available through
`get_assembly` and `get_function(section="brief")`; `get_candidate` explicitly loads baseline evidence.
Preparation is described as deferred, never falsely queued or completed. Low-level claim/UI APIs retain
full briefs. The read-only `get_claims` tool lists up to 20 of the caller's live project claims and tokens;
ownership errors identify held functions and point to this recovery path. No other agent's claims are exposed.

Explicit CLI-reported MCP timeouts/unavailable tools are connection outcomes rather than no-compilation
failures. During connection backoff, saved source and claims remain available; an explicit user stop still
uses ordinary cleanup. A second consecutive CLI-reported connection failure pauses further model retries;
normal paused-worker cleanup releases leases without abandon penalties. Old session replies cannot trigger
a new connection outcome. Generic blockers and provider usage limits retain their existing handling.

`try_compiler(compiler="current")` uses the effective function profile (including overlay selectors), retaining
its flags unless explicit flags are supplied. Imported projects resolve their file's own compiler through the
console adapter and keep their build-owned flag restrictions. Explicit compiler diagnostics rejecting a
requested option are remembered for that pinned compiler ID; repeated diagnostic requests skip that known
invalid flag, without claiming a successful compile or match. Other compiler versions and unrecognized
failures are unaffected. Mismatch-focused assembly reads without a mapped target anchor return an explained,
bounded raw target page; they do not infer a location or run another compile.


## Paused-queue read snapshots

Automatic selection reads lightweight pause records before loading full sources. Records without an exhausted
current or historical count need no fingerprint. A batch shares header contexts, symbol indexes and scope
revision reads while retaining byte-for-byte equivalent per-function signatures. Unchanged ROM decisions use
the existing bounded single-flight read cache, keyed by recorded model/worker identity, input revision, project
configuration, exact pause records and repair-rule digests. Returned IDs are copied; callers cannot mutate the
cached result. Retry/resume edits and source, target, header, symbol or matched-callee changes invalidate the
snapshot. Imported projects recheck external source/build dependencies on every decision rather than caching a
pause behind a time limit. These are scheduling hints only; no scores, compile limits, claims or judge verdicts
are changed.

This addresses observed empty-queue calls with roughly 1,150 database reads and up to 88 seconds of elapsed
work under contention. A synthetic 40-function regression uses 63 reads for a cold complete decision and 11
for an unchanged same-model worker, compared with 360 reads for the individual fingerprints alone. These
query counts do not predict wall time on a user's machine.


Idle receipt revisions also seek pause records through the existing settings primary key. The legacy exact
key and scoped `function-id:` range are separate, disjoint lookups, with a stable final key order. This avoids
cross-scanning unrelated settings for every project function; function 1 cannot consume function 10's scopes.
The complete legacy/model/worker key set and resulting work revision are unchanged. A synthetic 120-function,
5,000-unrelated-setting regression executes about 4,500 SQLite VM instructions instead of 7.4 million. This
follows slow-read logs attributing 12–79 seconds to the previous variable GLOB/OR join under live contention;
synthetic wall times are not a live performance guarantee. No schema migration or extra index is required.

### Include the lease token when handing work off

Recent transcripts showed five first release calls rejected after the same worker
reclaimed a function: agents supplied notes but omitted the claim token already
present in their continuation state. Move-on feedback now provides a release call
with the current function and lease token. Tool instructions ask for that token on
the first call as well as retries. A delayed call with an old token still cannot
release a newer claim; guidance neither acquires nor releases work. Synthetic
regressions execute the suggested arguments across reclaim generations and verify
that other workers cannot obtain a release token through this guidance.

### Repair invalid batch anchors in one response

Two recent independent patch batches failed on missing or ambiguous anchors without
identifying the failing variant. Batch validation now reports every invalid variant
by its one-based position, retains exact occurrence/line diagnostics and includes the
current candidate revision. A rejected explicit batch runs no variants or baseline
build and changes no candidate; valid alternatives are not silently compiled on their
own. Fix each reported anchor against the same starting C and resubmit the batch.

### Reuse discovery fingerprints within a pass

Recent candidate reads took 15–21 seconds and about 1,000–1,200 SQL reads even when
no new build ran. Proposal eligibility and final filtering rebuilt the same
source/build/rule fingerprint for each candidate. They now compute revision scope
once per pass and reuse it for indexed tried-target lookups, as already done for
the durable compiled-source scope. The database lookup still runs for each target;
trial records are not cached. A later discovery pass recomputes the fingerprint,
so changed compiler inputs reopen old trials.

Synthetic 100-proposal filtering needs four scope calculations instead of 103;
generation eligibility needs three instead of 102. Regression tests verify the
same suppressed/remaining proposals, fresh trial records and changed-input
invalidation. These counts do not establish a live wall-time speedup or explain
all observed candidate-read latency. Every submitted candidate still uses the
ordinary judge.

### One copied callee differs from the target

A reused function can have the right structure but still call a donor overlay's
function. The existing overlay alias family only covers the same address under
another name. `target_callee_name` additionally offers a single spelling replacement
when the MIPS target and declared candidate calls differ by exactly one symbol and
have matching static call counts. It rewrites the owned prototype and calls together,
without altering shared headers or guessing among multiple targets. Call-focused
search prioritizes this hypothesis; acceptance remains the ordinary byte judge.
