# Compact search history

Search memoization already uses source/input hashes, not stored C comparisons. Keep this
memory: deleting it would make searches repeat expensive compilations. A hash is advisory
cache identity, never proof of an exact match; improving candidates still pass the judge.

## What remains

- Candidate SHA-256, attempt ID, score, settings, timestamps and metric counters.
- The latest candidate and latest compiling candidate for each agent/function.
- Improvements, exact matches, current best/seed/matched source and explicitly referenced recovery evidence.
- Useful bounded local repair feedback and the existing input-scoped negative search cache.

`source_digest` is identity only. `source_hash` remains a reference to recoverable compressed
source. Removing a payload must clear the latter while preserving the former. Never use a
hash-only record as if it contained source; recovery and repair capture select available sources.
Old rows with unknown improvement status remain conservative until a human can classify them.

## Cleanup and limits

Settings → Database storage → **Clean old unsuccessful source** also expires superseded
compiled non-improvements, not just compile errors, and reduces oversized deep-search feedback.
The default retention is zero days for an unconfigured installation; existing saved age rules
are preserved. Cleanup still runs only when requested or when the user enables daily cleanup.
A zero-day rule keeps the active/recoverable candidates listed above.

New deep-search feedback over 16 KiB keeps its score and output fingerprint instead of full diff
rows. Smaller local feedback remains available for follow-up repairs. Source-based review proposals
and successful compiler evidence are outside this lossy policy.

Transcript age limits already exist separately under database cleanup. Keep completed-session
usage totals and ongoing conversations when expiring transcripts. Do not indiscriminately delete
activity events: they also drive claim selection, release receipts, polishing and room replay,
not just the visible timeline. Their semantic records and metrics remain intact.

## Shared payload collection

`payload_links` records each live reference atomically using database triggers. Explicit cleanup
backfills legacy links in resumable 256-record batches, then uses the hash index to collect orphan
payloads in bounded transactions. No reference backfill or whole-history source decode runs at startup.
Lazy evidence tables install their link triggers too. Schema changes during collection restart reference
discovery before more deletion. Cancellation retains completed work and cursor state.

Cleanup creates reusable SQLite pages; the file does not necessarily shrink. Use the separate,
explicit **Compact database files** action to return those pages to disk when writers can wait.
