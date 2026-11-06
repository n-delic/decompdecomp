# Work against the running app

For matching work on an authorized project, use the running app's judge. It already has the current
compiler profiles, declarations, symbols, original-image regions, claims and attempt history.
An isolated copy can miss a data region and report an instruction match without checking its constants.
Use throwaway projects for app development and tests; never open a second `RomNetwork` writer on the live database.

## Read a candidate, try C, inspect the best result

Run this with the Python environment that has decompdecomp installed. The example reads the running app's
local connection file; keep its contents private. It uses the same authenticated client as the CLI.

```python
import json
from pathlib import Path
from urllib.parse import quote
from decompnet.app.cli import _call

connection = json.loads((Path.home() / 'Decomp/.decomp/server.json').read_text())
slug = 'your-project-slug'
name = 'your-function-name'
query = 'slug=' + quote(slug, safe='') + '&name=' + quote(name, safe='')

project = _call(connection, '/api/project?slug=' + quote(slug, safe=''))
function = _call(connection, '/api/function?' + query)
source = function['source']  # Edit this C using the target and source context.
try:
    result = _call(connection, '/api/try', dict(slug=slug, name=name, source=source), timeout=120)
    print(result['text'])
    print(result.get('checks', {}))
finally:
    _call(connection, '/api/release', dict(slug=slug, name=name))
best_diff = _call(connection, '/api/diff?' + query + '&which=best')
```

Use the home selected when launching the app if it is not `~/Decomp`. Do not print the connection object.
Respect claims: a busy function belongs to its current worker until released.

During a saved-progress recheck, foreground judge requests can use the already-imported
layout, context and compiler inputs without waiting for every saved function to compile.
Saved verdicts do not overwrite a newer foreground match. Further file imports and Git
snapshots wait for the recheck to finish. If files change externally during this phase,
the request asks you to wait for synchronization and read the function again; retry
against those updated inputs. GUI function notes and releases remain available too.

`which=best` matters: the latest experiment can be worse than the saved best. A score-zero scratch attempt
on an already-matched function is not a new recovery. Compare the function's status before and after, and
verify that accepted sources and headers reach the saved project. Never change the target or comparison gate.

## Check settings and declarations before long searches

The permuter now runs at most four current-toolchain flag profiles before a long search. It caches the
actual source (including formatting), header, target, symbols, tool paths and compiler settings. Changed inputs
are checked again; the random phase also checks the candidate left by local repairs. Cancellation and variant
limits include the probes. Nothing is downloaded.

If automatic compiler changes are enabled, the existing profile application gate rejudges an exact-winning
profile and applies it to that function only. Partial improvements become scoped suggestions for review;
when automatic changes are disabled, exact winners also wait for review. Imported projects
keep their own build settings: use their file/compiler tools; the ROM preflight does not rewrite their builds.

Unresolved symbols have a separate diagnostic. Establish their addresses from the target's relocation evidence
before random rewriting. Deep search reports the blocker; ordinary agents and source/address repairs remain
available. Do not guess addresses to improve a score.

The shared repair families also try a real structure return where C explicitly lowered an existing record
into a final `*out = result` copy, and SDK-style inline word setters with typed pointer locals. They do not
invent or edit shared types. Registered local SDK source matching the function's name can be tried before
machine templates even when its old signature differs; library selection and compiler settings still apply.
A name is only a candidate lead: only the byte judge accepts it.

## A function match and a rebuildable export are different checks

For MIPS ROM candidates, the judge also reports standalone layout readiness. This advisory check notices extra
function bodies, code before the entry point, and constant pools that would need multiple bases within one
object section. The function remains an exact match when its linked code/data pass; layout blockers do not
silently change its score or turn it back into a near-match.

For a non-contiguous pool, reference an existing original data object separately or split the pools, then
rejudge. `layout_checked` means those local checks passed, **not** that the whole game rebuilds.
Other object formats currently report that this check is unavailable; imported projects retain their existing
objdiff and optional whole-image verification gates.

## Synchronize the standalone checkout

For a ROM project, start a verified repository sync with a clean exported Git checkout:

```python
job = _call(connection, '/api/repository-sync', dict(slug=slug, folder='/absolute/path/to/checkout'))
# Poll /api/job?id=<job['job']> until state is done or error; inspect result and skipped entries.
```

The app checks each accepted candidate and rebuilds the complete image before publishing changes. A compressed
cartridge export has two hashes: the expanded analysis image and the original cartridge. Sync compares project
identity with the original cartridge and verifies both input files independently. A mismatched or corrupted
input is rejected. A definite export-layout blocker is reported before spending time on a full build. A compiler prefix
needs verification by the checkout's builder: a curated build may already support normalization.

Review `accepted`, `skipped`, and `verified_build`, then commit the source changes when authorized. Sync does not
commit or push. Imported projects already keep source in their checkout and do not use ROM repository sync.


### Source repairs and compiler-profile matches

Deep search first tries an explicitly configured local source mapping for the selected function, keeping its real declarations and original line layout. It then runs bounded current-toolchain checks, and repeats those checks after saving improved local or random candidates before generating more mutations. Cached checks are scoped to the source and judge inputs. The existing time/variant allowance and automatic-compiler preference still apply; nonexact profile improvements remain suggestions. Imported projects keep their own build/profile workflow (see ROADMAP).

`POST /api/compiler` now reports `matched`, `newly_matched`, `score` and `match_cause` in addition to `said`. A new match caused by settings has `match_cause: "compiler_profile"`; changing settings on an already matched function has `newly_matched: false` and `match_cause: "already_matched"`. Read this result before submitting another candidate: the settings operation already judges and saves matching C.

Diff responses include advisory `source_diagnostics`, also shown in the diff panel and agent briefs. Raw offset casts warrant checking records and argument types; products warrant checking order and widths; reference cleanup paths still need comparison against retail. These diagnostics never supply match verdicts. Comparison operand-order and unsigned scalar product-order candidates use the ordinary judge, just like other local repairs. No reference source or SDK is fetched automatically.


### Target-guided runtime and address repairs

The current-toolchain preflight keeps its four-compile allowance. For small MIPS runtime instruction shapes with O32 evidence it prioritizes an IDO MIPS III/O32 `-O1` hypothesis and a generic native-width C candidate. A source-changing result goes through the existing source/compiler suggestion workflow; it is not directly promoted by a shape detector. SDK identities recorded by signature naming prioritize `-O1`; manually typed familiar names do not.

Shared MIPS draft repairs can replace byte-pointer base-plus-offset expressions with a directly declared target address only when matching target HI/LO relocation evidence proves the address. This is a candidate-local hypothesis, not a global rename or function split. Diff/brief/deep-search diagnostics also flag possible static-helper register specialization. All scores and matches still come from the ordinary judge. Other architectures retain their existing repair/profile behavior; imported projects keep their own compiler settings.

### Preserving explicitly selected object definitions

Settings → Local source preparation now accepts **Original object definitions**, one per line:

```
gManager @ manager.c
```

Paths stay inside the selected source/include roots. Existing symbol aliases map reference names to proven destination symbols. Only selected objects referenced by the prepared candidate or its macros are included; unrelated objects are skipped. Use single-object file-scope declarations, or supply a prepared subset when the original groups multiple declarations. The ordinary source candidate is still tried without those additional definitions if budget remains. Both alternatives use the existing judge, cache, claim and context-promotion rules; an exact function result still requires export/data-layout and complete-image verification.

Configured numeric macro values select conditional source branches before extraction, preserving original line numbers. Unsupported expressions or compiler-specific conditions without configured values produce an actionable error instead of guessing. A prepared subset remains the fallback for complex preprocessing. Diff/brief/deep-search advisories now flag repeated extra address loads as a reason to inspect original definitions versus extern declarations.

When a near-match differs only in compiler-generated helper calls, check the runtime-helper mapping hint before
permuting. Confirm the target call sites and helper ABI, use an explicitly typed call or independently verified
alias, and rejudge normally. A missing relocation is not a source-scheduling problem. Nearby exact-match profiles
are bounded trial priorities, not automatic range assignments. Keep original SDK typedefs (including unions),
macros and locals in the configured source context; preparation carries referenced declarations from the mapped
file without importing unrelated objects or bodies.

Preparation errors now show the first compiler error and a path to the complete private log under
`.decomp/logs/source-preparation/<function-id>.log` in the app workspace. Missing root declarations can cause many
later syntax errors; fix the first one before rewriting a function. Math aliases need correct prototypes too.
A sole allocation immediate discrepancy is a reason to inspect sizeof and the target call arguments, not to invent
fields. After a prepared-source improvement, bounded compiler checks reuse its saved private context, respecting
the same pass budget/deadline and compiler approval settings.

### Coherent reference context and bounded retries

Local source preparation supports optional `contexts`, one explicitly paired alternative per line:
`destination = reference_symbol @ relative/source.c | relative/header.h`. Up to two alternatives per function
are tried alongside the original context within the same budget/deadline. Files remain inside authorized roots.
Use complete compatible SDK revisions; the app does not invent structure fields or mix header/body versions.
Conflicting typedef and missing macro-like call advisories ask for inspection rather than rewriting declarations.

Same-file helper definitions can supply their actual ANSI prototypes. No missing signature is guessed.
Optional `helpers` uses `helper_name @ relative/file.c` to retain explicitly selected, referenced static helpers
for inlining, including their referenced macros/types. The existing single-emitted-function guard remains:
non-inlined extra function bodies are rejected. Imported builds retain their own headers and judge; private
reference headers cannot be installed into them by preparation.

Selected object definitions retain integer bit-pattern initializers and expression order verbatim. Do not
replace union-based constants with rounded decimal literals. The normal linked data and whole-image export
checks still decide whether their placement is valid.

Warnings about implicit declarations now appear in preparation logs even after successful compilation. An
input-change race can retry once with fresh destination state, spending the same compile/time budget. Claim
loss, compiler failures and imported builds are not automatically retried. No stale result can be promoted.

### Target-supported SDK I/O candidates

For MIPS targets, raw instruction words proving multiple uncached accesses prioritize a bounded `-O1` check
under an installed IDO toolchain. This is an I/O profile hypothesis, not SDK identity inferred from a name.
Prepared-source improvements already repeat preflight using the promoted source/header digest and the remaining
budget. Stale-input preparation retries remain limited to one; settings approval and claim gates are unchanged.

Local repair families can try numeric versus existing symbolic volatile MMIO expressions only when target machine
words prove the same address and width. They never create symbols or infer addresses from a numeric-looking name.
Candidate-local I/O status scalars can try register, volatile stack and ordinary cached storage; hardware accesses
retain their own volatile qualification, and address-taken locals are excluded. Existing all-byte response records
can replace consecutive field reads with an aggregate copy when target unaligned word-copy evidence supports it.
No fields, padding or packed attributes are invented. The ordinary judge decides every result.

Diff panels, briefs, deep searches and preparation logs advise checking SDK constants, polling masks and added or
missing recovery branches before broad permutation. These advisories do not replace constants or remove paths.
Imported MIPS builds can use available target evidence for the source families, but keep their own compiler settings.
Other processors retain existing repairs: these target-evidence recognizers need processor-specific adapters.


## Keep source bodies and headers separate

For a local ROM project, submit a body and its private declarations directly. Do not concatenate the
header into repeated scratch copies of the body. Original line breaks and any supplied `#line`
directives remain intact. An exact result saves only this function’s source, header and profile together.
A miss leaves its working candidate unchanged. Each string is limited to 1 MB; imported builds retain
their own headers and currently do not support this path.

```python
job = _call(connection, '/api/source-preparation', dict(
    slug=slug, action='try-pair', function=name,
    source=Path('candidate.c').read_text(), header=Path('context.h').read_text(),
    # Optional: settings={'toolchain': 'installed-compiler-id', 'flags': ['complete', 'flags']}
))
# Poll /api/job?id=<job['job']>; inspect matched/score in the completed result.
```

Settings offers the same operation under **Local source preparation → Try a source body and header directly**.
The optional flags field uses the function’s selected compiler. Without it, the complete selected profile is used.
Claims, single-function emission, normal linking/data checks and atomic publication still apply.

**Fresh check & apply source, header & flags** can recompile a complete historical exact bundle after
inputs have changed. It does not trust the old verdict: the current target/data must pass again, and any
change during compilation aborts publication. The strict legacy `apply-review` action still rejects stale inputs;
the explicit fresh action is `recheck-review`. Old exact bundles are listed before scored misses.

Mapped preparation may also try up to two private headers captured from nearby exact functions when a
record type and target call overlap. It uses same-project verified snapshots, not a mutable shared header.
These remain ordinary candidates within the existing pass budget; type-name similarity is not a match.


### CPU context and export recovery

A local source preparation pass tries actual static helpers with original line numbers, private boolean source
switches, include prototypes (including pointer returns and aggregate arguments), and consensus callback bindings
from exact neighbours. Explicit source overrides always win. Failed source/header/profile trials remain cached.

For an exact ROM function blocked by constant-pool placement, open source preparation, enter up to 20 matched
function names, and choose **Repair export constant pools**. The API equivalent is POST
`/api/source-preparation` with `slug`, `action: "repair-export"`, and `functions: ["func_80001000"]`. Follow the returned
job. The bounded pass reads existing target constants, tries private scalar-reference alternatives, and keeps a
rewrite only if linked bytes/data and export layout pass again. Coverage and readability do not increase.
Retry repository sync afterwards: its complete original-image build is the final gate. Ambiguous pool values or
unsupported arithmetic remain manual work. This pass needs the updated app; it does not restart an installed one.

### Apply a compiler experiment to one function

`compiler_experiments` and `try_compiler` remain diagnostics. After an exact trial,
retrieve its exact C with `get_compiler_trial`, then call `suggest` with `function`,
`compiler`, `flags` and that unmodified `source`. Omit `ranges` for a function-only
change. A range wholly contained in that function also takes this path.

With automatic compiler changes enabled, the suggestion rechecks the proposed source
and captured header using the selected profile, then atomically saves the exact
source/header/profile for that entry. It does not wait for neighbours or change the
project default. Claims, current inputs, boundaries and linked ROM data are checked;
a failed recheck leaves the selected source and settings unchanged. Wider ranges still
need neighbouring evidence. Imported projects retain their existing file/build workflow.

With automatic changes off, the suggestion waits for **Apply**, which performs the same
fresh verification. Existing single-function range suggestions with a saved origin C
also use this path when applied, even if their neighbour check is waiting or was rejected.
Those old checks have no historical private header, so their saved C must verify against
the current header before publication. Already-matched functions are not overwritten.
A completed single-function check is labelled separately from a confirmed broader range.

## Review and push existing commits

In project Settings, the Git repository card shows an unpushed count based on local
origin refs. Open the count to review commit messages, including the agent/method
credited by automatic match commits and compact repair-example summaries when available.
Repair-example numbers refer to local records; source, assembly and full archives
are not copied into commit messages.

**Push commits to origin** refreshes origin, checks for divergence, then sends the
existing commits from the current branch. It does not commit or stage working files.
New commits created after the push begins remain for the next push. Imported build
projects can use the same push-only control for their existing source commits.

### Set up Git for a portable save

Save location and Git settings share one panel. Save now remembers the folder; Create Git repository initializes it and adds progress-only `.gitignore` rules while preserving existing rules without staging or committing files. Commit saved progress creates a local checkpoint of manifest-listed C, headers and portable metadata. It leaves unrelated files out, refuses staged changes and never pushes. This checkpoint enables future automatic match commits. Adding origin is separate and never contacts the remote. A remembered backup becomes the ongoing progress folder only when its saved content agrees with the current progress (save timestamps and revision counters can differ). Review differing edits before adopting it. Separate rebuildable-export synchronization remains under Advanced; imported projects retain their source checkout for origin and push.

`GET /api/repository-setup?slug=...` reads local save/repository state; authenticated `POST /api/repository-setup` accepts `slug`, `action` (`init`, `checkpoint`, `origin`) and an `origin` address for the last action. Mutations run as background jobs under the shared repository lock.
