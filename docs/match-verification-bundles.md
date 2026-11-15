# Last exact verification

A function's detail panel can download its last exact verification record. It includes the
source and header used, compiler profile and installed-tool identity, verification time,
context/unit, and hashes of target bytes, relocations, symbols, source and header. Target
ROM bytes are not included. Project-mode records still require the project's own build checks;
they are not a complete archive of every build dependency.

The panel compares current inputs with this record and explains source, header, compiler,
target/address, ABI or symbol changes. Keeping the evidence does not freeze progress or
change how candidates are judged.

Saved progress includes a `verification` section with `matches` keyed by function name and
hash-keyed, deduplicated `sources` and `headers`. On import, checksums and basic metadata
are validated. Imported records are marked historical. They cannot set matched status,
apply compiler settings, choose source, or bypass recompilation. A new exact local judgment
supersedes the historical record. Older saves without this section remain supported.

Only the latest exact record per function is retained; this is evidence for diagnosis,
not a replacement for Git history or a complete reproducible-build archive.

A new failed judge/build result for the captured local exact source creates a regression alert.
It appears in notifications and the function's verification panel with its time and score (or
build/scoring failure). Repeated failures update the existing alert. A new exact result clears
it. Different candidate source and imported historical records do not establish a regression.
The alert does not change code, compiler settings or matched status by itself.
