# Restore verified names in a batch

The window-authenticated `POST /api/symbol-names` endpoint accepts a local ROM
project slug, a `names` array of `{old, new, address}` entries (integer addresses),
and an optional evidence `message`. Set `preview: true` to validate the mapping
without changing anything. Otherwise the response contains a background job ID;
read `/api/job?id=...` for progress and the result.

The endpoint does not discover names. Supply reviewed source/object or debug-symbol
evidence, keeping ambiguous names out of the batch. It rejects stale addresses,
duplicate names and collisions. It uses the existing `rename_symbols` engine to
rewrite shared headers and sources together, checks every affected match once,
and publishes saved progress only after successful verification. A rejected
regression leaves the naming transaction unapplied. Batches run serially.

Imported projects and room guests retain the individual `rename_symbol` tool;
their build/file and host synchronization mechanisms do not yet support an atomic
bulk operation. Never use a second database writer to bypass these checks.
