# Candidate and repair storage

Attempt IDs, foreign keys, counters and timestamps remain in their original tables. Identical
UTF-8 payloads share one SHA-256-keyed zlib blob in `text_payloads`. Reads verify the checksum.
`attempt_details` and `repair_details` expose exact original text, including legacy unpacked rows.
Store connections route application `FROM/JOIN attempts` and `repair_examples` reads through these
views; explicit `main.attempts` and `main.repair_examples` access physical metadata for maintenance.
Use Store connections for application reads and writes: raw SQLite clients do not register codecs.
This storage format requires an updated app; preserve a database backup before downgrading.

New writes are compressed transactionally. Settings → Candidate storage → Compress existing history
backfills old rows in cancellable 50-record batches. No historical payload scan runs during startup.
Repair list, compiler-profile and header-reference queries use the small metadata JSON, while detail,
replay and exports recover the complete captured evidence. Attempt history uses 100-record keyset
pages with the existing covering `idx_attempts_history`; source is fetched for one attempt on demand.
The storage endpoint reports its EXPLAIN QUERY PLAN. No speculative indexes are added or existing
indexes dropped: the required history query already uses a covering index. Packing runs bounded
`PRAGMA optimize` to refresh planner guidance.

Retention is disabled by default. Users can remove source for old, unsuccessful attempts with no score,
or save a daily automatic rule. Selection and clearing happen in one transaction per bounded batch.
Current seed/best/matched source, the last attempt for each worker/function, all foreign-key references,
known attempt-reference columns and nested repair evidence references are protected. Attempt rows and
lifetime metrics remain. Stopping keeps completed batches; no rollback of earlier batches is implied.

Packing and cleanup make pages reusable inside SQLite. The existing Settings → Saved C header history
→ Compact database file action returns them to disk. Run it when workers are idle: SQLite VACUUM blocks
writes, requires temporary disk space and is atomic if interrupted. This change does not run compaction
or restart the user's app automatically.
