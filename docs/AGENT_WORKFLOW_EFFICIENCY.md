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
