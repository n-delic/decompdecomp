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
