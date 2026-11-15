# Compiler comparison startup

Compiler comparison and compiler-panel refreshes read current database state under
`progress.view()`. They do not implicitly restore/rejudge the project's saved files.
Normal saved-file watching continues independently. Applying a result still checks
fresh source/settings and rejudges before changing its compiler profile.

Installed-compiler comparison uses console capability checks and any already cached
map solely for optional queue priority. It does not wait for compiler-map construction.
Profile usage loads function identities and ROM offsets so overlay assignments resolve
correctly; legacy name/address assignments are indexed once rather than searched for
every function. Runtime availability is checked once per distinct toolchain per pass,
without a long-lived cache that would hide newly installed tools.

The initial queue contains only IDs, addresses and scores. Full records are fetched
in batches of at most 25, and eligible records from each batch are tested before the
next batch is inspected. Headers/symbols and previous comparison digests are read once.
Existing digest encoding remains unchanged, so completed comparisons continue to resume.
Preparation stages and elapsed timings appear in progress/detailed output, including
time to the first compile. Remaining work stays indeterminate until eligibility is
known. The final total/skipped/deferred counts remain exact, including when a run limit
requires further eligibility counting after its compiler work has finished.

Tests enforce no implicit saved synchronization or cold map construction, compilation
before later eligibility batches, bounded profile-override scans, per-tool runtime
probes, overlay identity, completed-result reuse, auto-apply guards and cancellation.
Live performance depends on storage, installed compiler runtimes and concurrent jobs;
these changes do not remove actual compiler execution or weaken byte verification.
