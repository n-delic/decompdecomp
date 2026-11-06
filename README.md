<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
    <img alt="decompdecomp" src=".github/assets/logo-light.svg" width="560">
  </picture>
</p>

<p align="center">
  <a href="LICENSE"><img alt="AGPL-3.0 license" src="https://img.shields.io/badge/license-AGPL--3.0-157a49?style=flat-square"></a>
  <img alt="Linux" src="https://img.shields.io/badge/platform-Linux-15140f?style=flat-square">
  <img alt="macOS" src="https://img.shields.io/badge/platform-macOS%20Intel%20%2F%20Apple%20Silicon-15140f?style=flat-square">
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-15140f?style=flat-square">
  <a href="https://discord.gg/hhEm3zZagB"><img alt="Discord" src="https://img.shields.io/badge/Discord-join-5865F2?style=flat-square&logo=discord&logoColor=white"></a>
  <img alt="Agents: Claude Code, Codex, opencode, Antigravity, ZCode, any API model" src="https://img.shields.io/badge/agents-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20opencode%20%C2%B7%20Antigravity%20%C2%B7%20ZCode%20%C2%B7%20API%20models-157a49?style=flat-square">
</p>

decompdecomp is an app that decompiles games you own. AI agents write C or C++ for each function of the game. The
game's original compiler builds it. When the result is the same as the game, byte for byte, the function is
matched. In the end you get a project that builds your game again, exactly.

**New in 1.0.3-alpha: much faster, in its own window.** The app is ready the moment it opens (3.7 s before, 15 ms
now). The map of a big game updates ten times faster after a match. With 16 agents on two games, the app answers in
under a tenth of a second (1.4 s before), and agents restart by themselves after a crash or a usage limit.

<p align="center">
  <img alt="decompdecomp on Demo Quest 64: the autopilot matches 20 functions for free, then a helper matches six more, each judged byte for byte." src=".github/assets/demo.webp" width="900">
  <br>
  <sub>The app on Demo Quest 64, the demo ROM that comes with it. To keep the recording short, the helper is a
  stand-in for a model that hands in the demo's own C; the app compiles and judges every function for real.</sub>
</p>

## Match through the live app

Local source preparation recovers referenced enums/object declarations, tries private MIPS symbol aliases only when instruction layouts fully agree, and combines independently verified small helper context with bounded compiler profiles. Source evidence locates branch/statement differences for review; it does not automatically rewrite behavior. See [local source preparation](docs/LOCAL_SOURCE_PREPARATION.md).

Reviewed MIPS raw tails can be recovered through the coverage panel or operator API, with staged disassembly, unchanged original prefixes and export guards. A known-demo corruption benchmark measures detection independently of matching; see [coverage review](docs/CODE_COVERAGE_REVIEW.md).


Local MIPS ROM projects expose incomplete function boundaries and raw-region
coverage separately from matching progress. When the detected queue empties, the
overview runs a bounded CPU coverage audit; it never infers whole-game completion
from an empty queue or a byte-identical build. See [code coverage review](docs/CODE_COVERAGE_REVIEW.md).


[The live API workflow](docs/LIVE_API_WORKFLOW.md) shows how to use the existing judge, preserve attempt
history, distinguish linking blockers from instruction differences, and synchronize a verified standalone build.
Compiler preflight checks precede long ROM searches; typed SDK and structure-return hypotheses share the ordinary
repair pipeline. A function match now carries a separate MIPS export-layout check, with a full rebuild still required.

## Review incomplete targets before tuning

MIPS ROM projects can audit unanalysed binary gaps from Settings without agents or
compilers. Incomplete target extents receive a visible boundary-review warning instead
of an automatic assembly verdict. See [ROM code audits](docs/ROM_CODE_AUDIT.md) for
limits and the read-only command-line audit.

## Automatic repairs before paid agents

Open **Regenerate machine drafts** under a project's overview, then choose **Regenerate drafts** to refresh
starting C for functions without compiling C and test it with the project's compiler. Select **Include all unmatched functions** for a full refresh. Room members can run this locally, with potential matches confirmed by the host. Matches, best attempts and shared headers are preserved.
Claimed functions and unresolved boundaries are skipped; Stop keeps completed work. **Starting C diagnostics**
in a function shows separate generation, compiler and linking failures. Unchanged failed inputs are cached;
explicit regeneration opens a new pass. No AI calls are made.

Initial MIPS drafts can try private callback-table declarations, missing forwarded arguments, and overlapping
stack views in one original-sized frame. Known-size original tables, constants and strings use the selected
overlay's reader. Malformed irregular switches get one bounded alternative. These are hypotheses: compilation
is a useful starting point, and only the ordinary exact judge can accept a match.

ROM drafts preserve overlay branch labels and follow renamed entry symbols. Existing imports can recover missing
labels only at recorded instruction addresses, including supplied jump-table cases. Cached failed drafts are
regenerated after the fix; newly recovered local drafts go through the ordinary compiler and exact judge.
Outgoing branches and incomplete targets still need boundary review.

Source preparation can now find real matched callback owners as CPU-only compiler
context, including saved C from another unit. It can separately assemble supplied
local assembly and verify linked code/data without claiming C coverage. The overview
separates exact C, verified source assembly, other assembly and open functions.
See [local source preparation](docs/LOCAL_SOURCE_PREPARATION.md).

Register-only MIPS near misses now identify closed register swaps from actual linked bytes,
including cases obscured by relocation-label rendering. Deep search prioritizes up to 24
lifetime candidates within its existing budget. Optional installed-GCC captures expose actual
allocation order and conversion lifetimes; both diagnostic compiles count against that budget,
and cached evidence is invalidated by changed source/build inputs. Agents can read a compact
summary without starting tools. A bounded repair can relocate an already-present, proven zero
identity; it does not invent padding expressions. See [register-cycle replay evidence](docs/repair-replay.md#register-cycle-lifetime-search).


Source preparation retries bounded compiler profiles after recovering a better
source context, restores actual small getters as well as call wrappers, tests a bounded private
helper definition-order alternative, and ranks
compiled source-identity evidence before broad mutation. Scalar-pool repairs can
try per-use precision and signed constant bindings; near scheduling differences
also get token-preserving statement line variants. All still require exact judging. A small verified predecessor context can reproduce
internal compiler alignment; each predecessor is rejudged and only the selected
entry is exported, without adding padding to source. For IDO private calling
conventions, the app can also read explicit procedure metadata from `.mdebug` and
verify a bounded real caller/callee unit. Every body must match its known ROM
entry; only the selected function is published and exported. Compiler context
is not counted as additional progress.

Iterative repairs now interleave first-block and whole-function priorities, reserve bounded
regional combination probes, and show compact mismatch clues to agents. Captured fixes
can test single-edit and pair subsets without model calls. These are search heuristics,
not a demonstrated speedup for every checkpoint.

Local allocation search now includes initialized pointer guards around identical call branches,
CFG-proven value-phase splits paired with disjoint lifetime reuse, and groups of up to four
independent pointer/scalar initializers. A small regional frontier can retain a temporarily worse
score when it repairs a different target block, within the existing budgets. All candidates still
need the ordinary exact judge. The [read-only linked-ROM benchmark](docs/repair-replay.md#read-only-linked-rom-benchmarks)
measures these combinations without agent requests or changes to a live project.


New captured matches can run bounded local edit minimisation and paired GCC diagnostics in the background.
Repair examples shows the saved analysis and its on/off control; successful game source stays unchanged.
Learned sequences use compiler and mismatch-shape evidence, and metrics expose wins per compile and per
measured trial minute. No model tokens are used for this analysis.

Register near misses now include compact allocation clues and bounded pointer-lifetime, mixed declaration-order,
byte-fill constant and independent initialization-order repairs. The search retains different register/spill
layouts alongside the numeric winner. Opt-in iterative autopilot can chain four edits within its existing
per-function compile allowance. Exact acceptance still comes only from the ordinary judge.

Local repairs now include same-type scalar-to-record forwarding, definition-scoped halfword
lifetimes and definite-assignment zero placement. Structured back edges support nested switches
and safe internal/outgoing gotos. Register-only near misses prioritize these candidates; the
normal exact judge decides every match. Random search retains its starting C as an explicitly
quality-unverified checkpoint, available through `get_function(section="quality")`, and rejects
new compound-update chains that can change meaning. Imported best drafts that become exact are
published under `src/` with their matched manifest before the save is acknowledged.

The bounded repair pass also tries equal-type chained local assignments in either order, final void calls as explicit returns, and terminal scalar bounds checks as ordered early returns. Empty polling loops can use an explicit entry guard, including in combination with the final-call rewrite. Each candidate is compiled and judged normally.

Candidate-quality reports are advisory: agents, autopilot and the deep permuter can keep working while a concern awaits review. Repair examples retains the warning and saved alternatives; exact matches still require the normal byte or objdiff judge.

The permuter's bounded repair pass tries unused scalar locals, local and parameter `register` storage, and adjacent
local declaration order before random permutation. It keeps original line layout and leaves shared headers alone.
These hypotheses run through the same compiler and exact judge as agents, in ROM and supported 32-bit imported
projects. They use local CPU, with no model API calls. Rejected candidates do not replace the best saved C.

The same pass can widen a byte/short local when arithmetic or a later conversion suggests truncation, test
candidate-owned callee parameter/return widths, and split or merge bounded integer lifetimes. It also tries
calls inside argument-selecting branches, copies terminal goto leaves, and reorders switch cases with no
fallthrough. Missing no-op zero cases can restore a compiler's original dispatch layout. Shared headers and
project-wide prototypes stay unchanged. These are hypotheses: an improved similarity score is still a draft,
and only a byte-exact result counts as matched. The families also reach iterative/deep search and
`try_local_repairs`; loops, incoming jumps, shadowing and ambiguous declarations limit what is proposed.

Deep search now revisits local repairs after a random rewrite improves the saved candidate. Each burst
tries at most 32 proposals and shares the original time/compile allowance. Small constant/copy reuse and
final integer shift/scale candidates target register-only differences without assigning machine registers.
A score-zero open function is still freshly verified before being marked matched. If an otherwise unusable
ROM draft conflicts with one plain shared return declaration, deep search can align the candidate to that
header and remeasure it. The old draft stays in history, the shared header stays unchanged, and the new
baseline may have a lower percentage. Ambiguous signatures still need review.

The repair pass can also keep a table slot address live before loading its pointer, combine unsigned
word temporaries with assignment reuse, and turn a returning guard into a forward jump. In-place final
shift/scale reuse can run inside a braced loop when an unconditional reset precedes every use and the
loop header and earlier body do not read the old value. Loop-carried values, nested/ambiguous loops and
gotos remain excluded. All candidates still use the ordinary exact judge.

When an agent reports a suspected invalid candidate, automatic claims, autopilot and the permuter pause
that saved source. Open **Agents → Repair examples** to read the report and compiled alternatives, then
choose **Reviewed: resume this candidate** if the warning does not apply. To select a corrective alternative,
use **Rejudge and select alternative** after inspecting its evidence. **Rejudge next batch** measures
saved alternatives locally, smallest functions first, without selecting C or clearing warnings. Finished
measurements persist and unchanged inputs are skipped on the next run; busy functions wait. Expand each
alternative to compare fresh/historical scores, the source diff and target assembly. It recompiles with the current judge
and can replace an incorrect historical best even with a worse score. Old source and warnings remain saved;
known, still-reported nonmatching shapes cannot automatically replace the correction. Reports are hypotheses; they never invalidate a match or replace
saved C automatically. Agents can use `report_candidate_issue` with an issue type, affected revision and
optional compiled alternative; each revision is checked against that function’s saved sources. Explicit agent claims remain available for investigation. Exhausted inputs retain
retry history when workers return to an earlier source, and changed build inputs reopen the search.
Claim briefs, candidate reads and resumed sessions show cached counts of remaining local repair proposals
without compiling them. Dependency pivots require a direct call in the original assembly, respect paused
candidates and run once per unchanged caller/callee inputs. Indirect, inline and header concerns stay in notes.
Repair examples separate missing baselines and unchanged C from source changes; captures already produced
by deterministic repairs are retained as implemented unless someone has deliberately reopened their review.

For close ROM misses it also tests cached compiler flag alternatives, two probes per pass. Later passes skip
already tested choices and can reach optimization levels as well as debug flags. Only exact results qualify for a
narrow compiler-range change. When automatic compiler changes are disabled, the result becomes a suggestion for
you to review. Imported projects retain their own build settings.

This cannot identify an unknown original-source function or reconstruct a substantially wrong algorithm. Those
still need source-assisted agent work. It also cannot guarantee that a nearly matching function will match.

Source preparation now advances through untested private profiles across later passes instead of
revisiting only the first cached choices. Each round keeps its existing two-new-profile allowance,
shared compile/time budget and explicit source/header/profile approval gates. Source-lead evidence
shows reference-versus-saved calls and concrete compiled control differences. Explicit helper
declaration conflicts suggest prototype/caller review before body permutation. Better saved drafts
stay selected; possible SDK/retail differences are advisory and do not generate branch patches.

Local ROM preparation can also try private declaration snapshots from nearby exact functions when
record names and target calls overlap. These are bounded hypotheses, never shared-header edits.
In **Settings → Local source preparation → Try a source body and header directly**, paste the two
separately and optionally enter a complete flag profile. A fresh byte/data judgment saves an exact
source/header/profile bundle together; misses keep the working candidate. Prepared profile reviews
can explicitly recheck old evidence against current inputs before applying anything.

Source extraction preserves file-level `static` declarations as well as function-local storage.
Source-lead notes flag a lost local static, extra compiled address-high loads around extern objects,
and explicit narrow MIPS prototypes where the target passes adjacent word pairs. These are review
leads, not automatic type or data-definition changes. Preserve authorized original initializers.

Saved-draft compiler preflight now resumes each completed profile across later bounded passes,
including after a stop. Improved source or changed judge inputs starts a fresh search. Exact results
still pass the normal compiler-change gate; partial results never change settings automatically.

Mapped source preparation preserves reached SDK macros from the mapped file's authorized include tree,
even with an explicit private header. Conflicting macro definitions require review. Small candidate-owned
pointer initialization helpers can be expanded as bounded, separately judged hypotheses; calls with side
effects, control flow, unknown macros and volatile arguments are excluded. Typed-object/base-folding notes
and bounded retail switch-table comparisons highlight context or revision differences; the CPU trials below
remain private hypotheses and skip unsupported layouts or missing case bodies. MIPS tables accept both named jump-table and address-based symbols.

In **Local source preparation → Prepared profile evidence**, **Fresh check & apply up to 20 exact bundles**
rechecks one saved exact source/header/profile bundle per function. Each is independently recompiled and
ROM-data verified before publication. Busy or stale failures are listed and leave other functions eligible.
Imported projects keep their build-owned headers and profile workflow.

**Settings → Local source preparation → Run a resumable CPU source queue** works through
mapped functions and saved exact bundles without model calls. It checkpoints each function,
resumes after restart, and shows matches, scores, errors and deferred busy work. Pause finishes
the current bounded check; resuming retains the original approval options. Each function gets
up to three preparation rounds, each at most 32 trials /180 seconds; ordinary compile timeouts
still apply. A new queue can revisit checked misses using cached trial progress.

The assembly checkbox is explicit: only mapped or prepared assembly candidates are included.
Misses remain set aside. Fresh exact C, private header and settings are published atomically
with the status change; failed claims, changed inputs or bad data roll the whole operation back.
Restart recovery releases only the dedicated queue identity's interrupted claim, never agents'
claims. Busy functions are listed as deferred instead of stealing their work.

MIPS ROM preparation discovers bounded actual caller/callee units and up to three real predecessor
functions from authorized mapped source. It preserves physical lines, static linkage, referenced
SDK macros/types and actual static helper bodies. Every emitted real body must independently
match its known original entry before only the selected entry is exported. No guessed padding
or target instructions are inserted. Up to sixteen explicitly selected coherent SDK source/header
alternatives share the pass budget; measured ranking is specific to the source, header, target
scope and compiler profile. A failed base mapping does not hide valid explicit alternatives.
Other processors retain single-body trials until their compiler-unit reader/judge supports them.

A function match is not a full cartridge verification. Use **repository sync** and its complete
original-image comparison before committing/exporting a verified checkpoint. Imported projects
keep their owned headers, compiler profiles and existing build workflow; a durable queue adapter
for those builds remains planned.

Mapped ROM preparation also queues bounded **CPU-only source recovery** trials before broader flag
searches. It can restore actual reference prototypes (including aggregate returns/arguments), preserve
a target call instead of a header macro, reuse verified named record declarations, try a small record
gap supported by at least two mapped memory operands, and test comparison constants/explicit array
extents from aligned instructions. MIPS retail switch tables can relabel source arms and clone an arm
only when an existing target arm has the same instruction structure and a bijective data-symbol map.
These are private hypotheses: unknown declarations, ambiguous line associations, unsupported arm shapes
and missing table readers are skipped. Reference files and shared headers are never edited.

Enable **Automatically recheck and save exact source/header/compiler bundles** in Settings → Local
source preparation for unattended promotion. This option is off by default and machine-local. The app
recompiles each exact profile bundle again, requires the linked-byte/data verdict, and atomically saves
its source, private context and function-only profile while rechecking claims and inputs. Approximate
scores never authorize a compiler change. Failed rechecks are scoped to current inputs and don't block
new candidates. Cached measured follow-ups resume in later bounded passes; register/stack near misses
reuse focused declaration, stack-padding and helper-expansion families. Imported projects retain their
existing build-owned header/profile workflow and require an adapter for these private-context trials.

## Changing a ROM project's compiler

Changing the game's compiler or an address range checks every affected matched function before saving.
If a match would break, the change is refused and the current settings stay in place. Narrow the range to the
part the new compiler actually built. Removing a range can explicitly reopen its affected matches instead.

Saved attempt scores are recalculated with the new settings. The C and attempt history stay saved; an old
percentage from different flags is not used to rank today's work. Whole-game changes run as a background job
with progress in Settings. If helpers change the code during a check, pause them and retry.

Exports also check each selected object has just its intended function definition. Constant pools use the original
addresses proven by the linked-byte judge, even when a separately compiled object's preferred section alignment
is larger. Pools that cannot share one verified base remain assembly fallbacks. The final whole-image comparison
still has to pass; isolated matches alone do not establish a rebuildable checkpoint.

## Choose work per agent

In **Start agents → Work selection**, choose a queue for the workers you are starting. Launch an expensive
model with **Largest first**, then start a cheaper model with **Untried first** or **Easiest first**. You can also
choose **Closest to matching**, **Finish source files**, or **Random**. **Automatic** keeps the existing adaptive
scheduler and model routing.

**Only functions below match %** restricts automatic claims to functions whose best recorded match percentage is
strictly below the threshold, plus functions with no scored attempt. For example, 80 includes unscored functions
and those below 80%, but excludes one at exactly 80%. This is the app's comparison score, not an estimate of work
remaining. **Untried first** means no compile attempts, and moves tried functions back rather than excluding them.

**Max tries per function** caps that worker's recorded candidate submissions on each function. Compile errors
count; the counter persists across sessions and restarts. At the limit, the best candidate and notes stay saved,
and the function is released for another worker. Raising the limit permits more attempts; blank means unlimited.
The final allowed attempt can still match. Compiler comparisons and flag searches are separate diagnostic work,
not candidate submissions covered by this cap.

Each worker's card in **Agents** has **Work selection** to change it independently. The setting survives sessions
and restarts and applies after the current claim finishes. Explicit strategies override adaptive routing and
release ordering; claim ownership and the worker's own recent-release cooldown still apply. Configured queues
claim one function at a time so neighbouring batches cannot bypass the filter. The percentage filter also applies to named function requests. The same settings work across consoles, ROM and project mode, and team rooms with
an updated host. Polishing uses its separate queue.


**Paused unchanged candidates.** Three unchanged releases, or reported exhaustion, pause automatic
selection for that recorded model. Other models can still try; workers using the same model share the
pause, including explicit effort qualifiers. Switching a worker to another recorded model does not
inherit the previous model’s release cooldown. Unspecified models use a worker-local pause. Source,
build/header, symbol, project-instruction or repair-rule changes make new inputs eligible. Old global
pause records are retained but are not attributed to models without evidence.

On a worker's **Agents → ⋮ → Retry paused functions** action (also on waiting cards), choose up to
50 functions; the default is five. Each free function meeting that worker's filters gets **one fresh
automatic claim** for that model. Grants survive restart and are consumed atomically when claimed,
including if a worker subsequently crashes. This bypasses only those functions' recent-release
cooldown. Active claims, compile/attempt limits, match filters and never-tried filters remain in place.
Waiting workers check within 30 seconds; stopped workers stay stopped. Normal provider charges apply
when running agents resume. The button does not start an agent itself.


For local library research, see [Comparing local SDK libraries and source](docs/local-sdk-comparisons.md).

## RetroReversing fork

This fork of [NDelic's decompdecomp](https://github.com/n-delic/decompdecomp) adds project import, ROM handling and matching features. Native macOS support is now upstream.
The original decomp.wtf and decompdecomp credits and licenses are preserved. Report fork issues to
[RetroReversing/decompdecomp](https://github.com/RetroReversing/decompdecomp/issues).

## Independently linked code images

Projects with compressed game code or overlays can export their verified linked functions through
`decompnet.forks.linkedimages.export`. These are normal objdiff units: original-image bytes form immutable
references, and only source-backed, relocated bodies that pass the project's image gates become candidates.
Assembly passthrough functions remain in the denominator without counting as recovered C. Source checksums
invalidate stale candidates. Population identifiers distinguish overlays even when their RAM addresses overlap.

The overview shows code-byte-weighted totals and a breakdown by image. This covers known function extents;
data and unidentified regions are excluded. These special image snapshots are read-only in the app: change their
sources through the project's promotion/build workflow and use **Check again**. Ordinary object units keep their
existing agent workflow. See [the export contract](docs/linked-image-progress.md).

## Linux-only compilers on macOS

In **Compilers & tools**, choose **Prepare Linux runner**, then install a compiler
marked **Linux VM**. This optional local Colima runner uses a pinned Linux image;
compiler jobs have no network or host-folder mounts. Native compilers still run
directly. See [setup, coverage and resource limits](docs/linux-compiler-vm.md).

## Imported project builds

Builds use an existing `.venv` or `venv` in the project for its Python tools and dependencies. The app does not
install those dependencies into its own runtime. Failed builds retain the first diagnostic even when later
warnings or extraction progress would otherwise hide it.

Automatic references can cover shared source directories and per-segment overlay directories. When a source
rebuild is incomplete, references are derived independently from the pinned original image: each resolved code
and data section must match its original bytes before any configuration is published. Candidate objects never
supply reference bytes. Function matches remain separate from a successful whole-game rebuild.

## Autopilot on imported projects

Autopilot can try machine drafts in imported projects with writable C files and a supported draft decompiler.
For source-less units with decompdecomp's verified Splat references, it creates separate C candidate files and
checks that the project's own build can compile them before enabling attempts. It preserves existing source,
Makefiles, compiler settings and the assembly used for the linked baseline. Unsupported builds report what
blocked preparation; zero attempts are not described as a completed search with no matches. Drafts still pass
the same compiler and objdiff judge as agent submissions. A matching candidate is not a full linked-ROM rebuild.

## Free attempts from similar code

Autopilot tries verified C from compatible games, tiny-function templates, and locally supplied library C.
Every accepted candidate must compile and link to the destination's exact bytes. No AI tokens are used.

- **Better near-miss repairs:** eligibility uses percentage similarity, so a single changed operand no longer
  excludes an otherwise close function. Assembly differences guide casts, signedness, literal and ordering edits.
  Agreeing high/low address references can also propose a known destination symbol or an automatic address name, alone or combined with a
  constant or explicit field-offset edit. Literal pointer addresses can be corrected too.
  Literal base-plus-offset expressions and array indices use the stride evidenced by the old compiled address;
  adjacent symbolic-high/numeric-low address pairs account for signed low-half carry. These can combine with
  explicit field-offset edits. Small candidate-local structs can also have byte padding resized or inserted
  before a used field, based on aligned memory operands. These proposals support simple scalar/pointer fields
  and common fixed-width aliases; packed types, bitfields, unions and ambiguous offsets are skipped. Shared
  headers are never rewritten. Ambiguous mappings, dynamic indices and unproven strides are skipped.
  Numeric ID macros used once to index an explicit 32-bit table can be corrected from aligned word offsets;
  ambiguous declarations, macro uses and relocations are skipped.
  Saved best attempts get the same address checks before wider donor searches; probe feedback and failed edits
  are cached and share the ordinary search budget. These edits affect only candidate C; shared symbols and types stay unchanged. An already-installed decomp-permuter
  supplies a small additional batch when time remains.
- **Regional differences:** verified transfers teach symbol mappings and explicit pointer offsets. Offset
  mappings need two independent matching pairs and no conflicting evidence. Named struct definitions are not rewritten.
- **Tiny functions:** constants, arithmetic, getters, setters and simple forwarding wrappers can be proposed
  without another project containing matched C.
- **Local libraries:** add SDK archive folders once in global **Settings → Local SDK libraries**. Each game
  detects matching signatures automatically; its Settings can select libraries explicitly, including disabling all.
  For advanced source matching, in the project SDK panel, supply an object archive and optionally its C source file/folder and a
  self-contained declarations header. Matching fingerprints select C to try with the project's compiler.
  Sources stay local; nothing is downloaded or built by an SDK's scripts. Import with the compiler settings you
  intend to use; reimport after changing them. Ambiguous definitions are excluded.
- **Better partial candidates:** after verified transfers, identical instruction fingerprints can reuse a
  better saved C attempt from another local game. The receiving compiler and judge must confirm its own
  improvement; donor percentages are never copied. Partial improvements stay unmatched, record their origin,
  and never provide naming or SDK evidence. Unchanged trials are cached; changed source, target, context,
  symbols or compiler settings allow another check. Each pass tries at most 64 partial transfers, within any
  shared free-search budget. ROM and imported projects can participate. Imported partials remain saved best
  attempts rather than replacing source files; large imported input trees are skipped conservatively.
- **Automatic follow-up:** a new verified match or better unfinished ROM candidate queues compatible projects on one low-priority worker, so useful
  code can spread without repeatedly clicking Run again. Stop pauses that project's automatic reuse until Run again.

Settings lets you disable automatic reuse and adjust its time and compile limits (defaults: two minutes and
128 attempts). A whole automatic wave shares one budget, including exact transfers. Manual autopilot keeps its
existing exact-transfer phase, then shares the additional budget between templates, libraries and similarity repairs.
The last search reports verified bytes, attempts and bytes per minute of elapsed time, not CPU time.

In Settings, optionally enable **deeper iterative repairs** after normal autopilot. This experimental phase is
**off by default**, with its own additional 120-second / 128-attempt budget (both adjustable). Normal matching
finishes first and keeps its full allowance; automatic background reuse does not start this extra phase.
Iterative repairs try up to three edit rounds and keep the best three improving/equal-score alternatives.
Each function gets at most eight new compilations per pass. A persistent round-robin cursor continues with the
next function on the next pass, instead of repeatedly prioritising the closest candidates. Unfinished frontiers
survive restarts. The phase records its own results and adds a summary note; only exact judge matches count.


Global-address repairs also track symbolic MIPS upper halves through straight-line instructions to later
load/store offsets. Register clobbers, branches, calls and unknown instructions stop tracking; conflicting
consumers are rejected. Full-address comparison also resolves C identifiers whose address is shown as another symbol plus an offset
in disassembly. Reconstructed addresses only propose local C edits and still require exact verification.

Repairs retain the original donor when the candidate's provenance is known. Successful repairs can then
teach symbol mappings for other candidates from that same donor. New data/callee mappings need agreement
from two independently verified function pairs; conflicts, reopened functions and changed donor code invalidate
the evidence. Each proposed transfer still goes through the exact compiler/link judge. Old saved candidates
can recover provenance only when recorded donor history reproduces their adapted C exactly.

Saved candidates also get bounded constant-only repairs when no address correction is proposed. Aligned
MIPS immediate and fixed-shift differences can guide literal, explicit multiply/divide and shift edits.
Repeated constants are tried individually (up to four occurrences), with small consistent group edits and
combinations capped at eight proposals. Comments, strings, symbol addresses and suffixes are preserved;
register/control-flow changes, signed expressions and implicit type/array-stride changes are left alone.

Saved candidates can also get a local MIPS signature/argument repair when their only non-relocated
instruction differences move ordinary integer/pointer arguments to later registers. The repair can insert an
unused parameter before a live incoming argument, or forward untouched incoming registers through a small
single-call wrapper. Only the candidate's declarations change; conflicting shared prototypes fail compilation.
Floats, aggregate/variadic parameters, stack arguments and complex wrappers are left alone. These shared rules
can help any compatible MIPS ROM game; they are not specific to a title, and other processors/object projects
need their own calling-convention and source-context support.

When eligible near misses exist, similarity transfers use at most three quarters of the remaining search budget;
the rest is reserved for repairs. The transfer cursor is retained so the next pass continues through the queue.

Use **Run again** to resume, including after restarting the app. Completed trials are remembered; changes to
candidate C, headers, symbol addresses, relevant mappings, targets or compiler settings make them eligible again.
A running compile or analysis step can finish after the time limit; no new candidate starts once it is reached.

A donor's proven flags are also tested with an already-installed toolchain. A byte match under those flags appears
as a **compiler** result with C saved in the function's notes. Review the settings before accepting it: these
trials do not change settings or count as matched progress. Similarity scores are never verified progress.

These additions support MIPS ROM projects (N64, PS1, PS2 and PSP). Imported repository projects still use exact
transfers; adapting similar functions across source files and object relocations is planned.

## ROM library

Set **Settings → ROM library** to the folder containing your extracted game files. When an imported project
needs its game, decompdecomp searches that folder recursively for the project's expected SHA-1 and fills in a
matching path. N64 .z64, .v64 and .n64 byte orders are recognized regardless of filename. Confirm ownership and
click **Use path** to continue. The search does not unpack archives or start a build.
It checks at most 5,000 files, 2 GiB of data, or 30 seconds per search; choose a smaller folder or select the
game manually if it reaches a limit.

If setup fails after accepting your ROM, reopen the project or retry: its verified copy is detected first and
**Retry setup** reuses it without opening the file chooser. Projects without a declared SHA-1 still need manual
selection. Clear the folder setting to disable library searches. On POSIX systems, hashes are cached in memory until files change
or the app restarts. Windows rehashes files because timestamps may not reliably reflect overwrites; original files are never moved by the search.

## Invite browser contributors

In a project's **Decompile together** section, start sharing and copy its invite link.
Friends open that link in a browser; no app installation or `serve` flag is needed.
**Open browser workspace** previews the same page. Keep the host app running and sharing.
A quick tunnel provides an HTTPS address; your own address works for reachable LAN/VPN peers.

Guests enter a name and select their own matching game file. Its hash is checked locally;
the file is never uploaded. Host approval, invite rotation and member removal use the
existing room controls. Browser sessions survive a page reload in the same tab.

The workspace lists functions, loads saved C and read-only shared declarations, and runs
**Compile & check on host** through the ordinary ROM or imported-project judge. Better
attempts and exact matches are recorded on the host under the guest's browser identity.
Other agents' claims are respected; release a function before checking another one.
Only one browser check runs in a room at a time. Unsent edits live only in the tab: copy
them before closing or disconnecting.

This is a contribution workspace, not remote access to the host's administration or agent
controls. It sends saved C and numeric verdicts, never game bytes or target assembly.
Compiler failure details remain on the host. Assembly inspection, context editing and
starting agents still require the desktop workflow. Browser-side disassembly from each
guest's own game is planned. Disc-project identities can refer to an extracted program
rather than the disc image: use the exact program the host imported (up to 256 MiB), not
an RVZ/compressed image. Projects without an identity retain the existing room join rules.

## Sign in with GitHub

In the native app’s **Settings → This computer → Sign in with GitHub**, register a GitHub OAuth App and use the
**Authorization callback URL** shown there. This is the trusted private remote address followed by
`/login/github/callback`. Set the OAuth App’s homepage to the same private remote address.

Enter the app’s **Client ID**, **Client secret**, and your allowed **GitHub username**, check **Use GitHub sign-in**,
and save. The app verifies and pins that account’s permanent numeric ID. The browser login page then offers
**Sign in with GitHub** instead of the code form. It requests no repository permissions. Keep the OAuth secret
in the native Settings fields, not in a repository or chat; it is stored only in the host’s private settings file.
Setup requires a registered OAuth App; decompdecomp does not ship shared OAuth credentials.

Only the pinned account can gain full web access, even if its username later changes. Login uses a browser-bound,
short-lived, single-use state and PKCE; GitHub tokens are used transiently to verify identity and are not stored,
cached or sent to the browser. Settings changes revoke browser sessions. The native desktop window remains
accessible to disable GitHub sign-in or fix its credentials. If the private remote address changes, update the
OAuth App’s callback URL on GitHub too. GitHub login needs an internet connection; existing offline snapshots
remain available under the offline-storage rules below. OAuth endpoints bypass the service worker so browser
redirects and authentication cookies are handled directly by the browser, including on iOS.

## Install on your phone and use saved views offline

Open the private HTTPS web interface in Safari on iPhone, sign in, then choose **Share → Add to Home Screen**.
On Android, use the browser’s **Install app** or **Add to Home screen** action. The installed app opens in its own
window with the decompdecomp icon.

After opening the app online, its interface, project list and recently viewed project, agent and function data
are saved on that device. Offline, you can reopen those saved views; a banner marks them as snapshots rather
than live progress. New views need to be opened online first. Compiling, agents and project changes require the
host Mac: offline actions are rejected and never queued or submitted later. Reconnecting resumes live reads.

Sign-out and an online authentication failure clear saved data. **Settings → Clear offline data on this device**
and the offline banner’s **Clear saved data** button clear it manually. Offline copies remain on the device until
cleared; disabling access on the host cannot erase a disconnected phone. Sign-in codes, API keys, ROMs and
uploads are not cached. Browser storage is bounded and may also be cleared by the operating system. A normal
desktop app window does not register the service worker or store these browser snapshots.

## Responsiveness with many projects

Project progress totals are maintained transactionally as functions change, so sidebar polls read
small saved summaries instead of rescanning every function. The sidebar batches its project reads.
Overview diagnostics and cross-project byte identity use bounded local snapshots; relevant edits
invalidate them. No Cloudflare cache is involved.

Map refreshes reuse unchanged snapshots and transfer only changed rows when the layout is stable.
Renames, splits, removed functions and missing snapshots fall back to a complete map. Live claims and
attempts remain incremental. Sidebar and live requests have capacity separate from heavier detail reads;
background compiler jobs leave one compiler slot available to foreground work.

Simultaneous identical sandboxed ROM compilation requests can share one compiler invocation. The key
includes project/function/target identity, compiler settings, environment and staged header contents.
Every request still links, judges and records independently. Later requests compile again; custom
commands/mounts/includes, explicit cancellation, diagnostics capture and time-dependent macros retain
private invocations. Imported project builds retain their normal build locks and verification.

See [the performance measurements](docs/APP_PERFORMANCE.md) for the synthetic benchmark and checks.

## Run without a desktop window

```sh
decomp serve --port 6464
```

Open `http://127.0.0.1:6464/` in your browser and enter the sign-in code printed in that terminal. This runs the
same project database, agents, compiler/judge and project/ROM workflows without a native window or tray. The
browser uses a separate HttpOnly session cookie for up to 12 hours; **Sign out** ends only that browser session,
not the server or its agents. Restarting the server prints a new code and requires signing in again. Keep the
terminal code private: it grants full access to this app, unlike project-scoped agent or browser-worker pairing.

To use the browser and desktop window together, open **Settings** in the desktop app and turn on
**Enable web interface**. Click **Open web interface**, then enter the sign-in code shown there. Both interfaces
share one running app, project database and set of agents. The setting is remembered across launches; each
launch creates a fresh sign-in code. Turning the toggle off immediately signs out all browsers while the desktop
app keeps running. For private phone or laptop access, enable **Tailscale Serve** on the host computer, enter its exact HTTPS
`.ts.net` address under **Settings → This computer → Private remote address**, and save. Run
`tailscale serve --bg http://127.0.0.1:6464` (use the app’s actual port). Both devices must be connected to the
same tailnet. Open the remote HTTPS link and enter the code from the desktop app. Tailscale provides the private
network and TLS; decompdecomp still requires its own sign-in. Public Funnel access is not needed.
Changing or clearing the trusted remote address revokes browser sessions. Disabling the web interface blocks
browser access even if the Tailscale proxy remains configured. Use `tailscale serve --https=443 off` to stop that
proxy. Remote addresses must be set from the native app; remote browsers cannot authorize a new destination.

`serve` uses the usual home (`~/Decomp` for an installed copy; `~/Decomp-dev` from a checkout). If an app is already
running for that home, it refuses to replace it. Stop that app deliberately with `decomp stop`, or choose another
home and port, for example `DECOMP_HOME=/path/to/server-projects decomp serve --port 6484`. A checkout can use
`uv run decomp serve --port 6474`. Stop the server with Ctrl-C or `decomp stop` using the same home. Updates preserve
server mode; they do not open a native window.

It binds **127.0.0.1 only**. Direct LAN binding and public HTTPS serving are not enabled. Private HTTPS through Tailscale Serve uses
the explicitly trusted remote address configured in Settings. A private SSH forward can reach a remote machine's localhost server while keeping the same forwarded port.
Browser file uploads still work; folder/file-path chooser buttons ask for a path **on the server computer**.
Opening its folder in a desktop file manager is unavailable. Compiler and platform runtime requirements remain
the same; only native-window/tray dependencies are bypassed at runtime.

## Install

decompdecomp runs on **Linux and Windows 10/11** on x86-64, and **macOS 12+** on Intel or Apple Silicon. On Linux, install it with one command. The installer also gets
the free compilers and keeps decompdecomp up to date:

```
curl -fsSL https://raw.githubusercontent.com/RetroReversing/decompdecomp/main/site/public/install.sh | bash -s -- --source branch:main
```

Upstream Linux packages remain available from the [releases](https://github.com/n-delic/decompdecomp/releases):

```
sudo apt install ./decompdecomp_*_amd64.deb        # Debian, Ubuntu, Pop!_OS, Mint
sudo dnf install ./decompdecomp-*.x86_64.rpm       # Fedora
chmod +x decompdecomp-*-x86_64.AppImage && ./decompdecomp-*-x86_64.AppImage   # any other distribution
```

On **Windows**, download and open `decompdecomp-<version>-windows-x64-setup.exe` from the
[releases](https://github.com/n-delic/decompdecomp/releases). Setup installs the Microsoft Edge WebView2 Runtime and
.NET Framework 4.8 when they are missing, then opens the app installer. It may ask for administrator permission and
needs an internet connection to download missing Microsoft components. The app installer lets you choose where
decompdecomp goes and whether to add a desktop shortcut (selected by default); it always adds a shortcut to Start.

To install from source with [uv](https://docs.astral.sh/uv/getting-started/installation/) instead, open a new
PowerShell window and run:

```powershell
uv tool install --python 3.12 "decompnet @ https://github.com/n-delic/decompdecomp/archive/refs/heads/main.tar.gz"
decomp setup
decomp shortcut
decomp
```

The app opens in a native Windows window. Its shortcuts appear on the desktop and in Start, and its tray icon
brings back a window closed while helpers work. Free tools use Windows builds where available. Projects stay in your Windows `Decomp` folder.

> [!IMPORTANT]
> **PS1, PS2 and most N64 games need WSL 2 on Windows.** Their period compilers (GCC for PlayStation, EE-GCC and
> CodeWarrior for PS2, KMC GCC for N64) use their Linux builds through WSL 2 on Windows. Without it,
> every compile of such a game fails. Set it up before you import one: run `wsl --install` in an administrator
> PowerShell, restart if asked, then open Ubuntu once to finish its setup.
>
> GameCube, Wii, DS and Windows PC games, and N64 games built with IDO, don't need WSL: their compilers run on
> Windows itself.

### macOS (Intel and Apple Silicon)

For an app you can put in `/Applications`, download the archive for your processor from the
[RetroReversing releases](https://github.com/RetroReversing/decompdecomp/releases):
`macos_arm64` for Apple Silicon or `macos_x86_64` for Intel. Unzip it, move
`decompdecomp.app` into Applications, and open it from Finder. The app includes its own Python and
dependencies, so moving it does not depend on a uv installation. It is not signed or notarized yet;
macOS may require you to approve its first launch in Privacy & Security. Install free compilers
and tools from the app's Setup screen. Replace the app from a later release to update it.

The source installer is another option. It creates a launcher in `~/Applications` backed by
an external uv installation. To install from a checkout:

```sh
git clone --branch main https://github.com/RetroReversing/decompdecomp.git
cd decompdecomp
bash site/public/install.sh
```

The installer uses uv and Python 3.12, installs the Cocoa/WebKit bridge, gets the free tools, and creates
`~/Applications/decompdecomp.app`. Open it from Finder or run `decomp`. Projects stay in `~/Decomp`;
toolchains live in `~/Library/Application Support/decompnet/toolchains`. Xcode Command Line Tools
(`xcode-select --install`) are needed on Intel Macs for N64's MIPS binutils, and on both Mac architectures to
build PS1 GCC 2.6.0, 2.6.3 and 2.8.0 from pinned source on first use. The ready-made 2.7.2 (PSX and CDK) and 2.8.1 compiler binaries are for
Apple Silicon. Finder launches preserve the PATH
from installation so your installed Node and helper CLIs remain available; rerun `decomp shortcut` after
changing their locations. This source install has no signed DMG.
Installs from `main` track that branch in this fork when checking for updates.

**Apple Silicon:** the app, WebKit window and modern tools run natively. The pinned N64 KMC-compatible
GCC 2.7.2 and binutils 2.6 archives contain Intel programs, so install Apple's Rosetta runtime before setup:

```sh
softwareupdate --install-rosetta
```

Read and accept Apple's license if you agree, then rerun `decomp setup`. No proprietary game compiler is
installed automatically. IDO still needs your explicit license acceptance in Compilers & tools.

| Mac capability | Availability |
| --- | --- |
| N64 ROM import, compile, match and export | KMC GCC via Rosetta on Apple Silicon; directly on Intel |
| PS1 GCC 2.6.0, 2.6.3, 2.7.2 PSX/CDK, 2.8.0 and 2.8.1 | Native Apple Silicon tools with maspsx; 2.6.0, 2.6.3 and 2.8.0 also build from pinned source on Intel Macs |
| PS1 GCC 2.5.7, EGCS 2.91.66 and GCC 2.95.2 | Linux/WSL builds; native macOS builds are not available yet |
| Native window, file/folder pickers and export downloads | Cocoa and system WKWebView, no GTK or X11 |
| m2c, decomp-permuter and objdiff | Pinned Mac/Python builds |
| Other consoles and public decomp projects | Only where their pinned compiler/tool builds support this Mac |
| PS2 Sony EE-GCC 2.9, 2.96 and 3.2 | Optional local Linux runner; install Colima/Docker, then Prepare Linux runner in Compilers & tools ([setup](docs/linux-compiler-vm.md)) |
| PS2 SN Systems EE-GCC 2.95.2/2.95.3 and CodeWarrior 2.3.3/3.0.1 | Pinned Windows compilers through wibo on Intel and Apple Silicon; CodeWarrior requires license acceptance |
| Keeping helpers working after closing the window | Run `decomp` again to reopen; no Mac menu bar tray icon yet |

For development: `uv sync`, `uv run decomp` (separate `~/Decomp-dev` state), and `uv run pytest -q`.

To avoid rebuilding a packaged app for each code update, open Settings → Run from Git / update when
idle. Choose a clean `main` checkout from the configured update repository (Git and uv required).
Choose separate `~/Decomp-dev` data, or explicitly keep this app's current projects/settings. This
replaces the current process when keeping current data. Separate development data launches another
app and leaves this one running. Neither copies/migrates data nor alters the installed app bundle.
New managed agent sessions and jobs are held until existing work finishes. Continuous background
passes may need to be stopped from their own panels. You can cancel while waiting. Once idle, the
app fast-forwards main, runs locked dependency sync, checks imports, then restarts with the chosen
home. If only the permuter worker's Python files changed, the source app fast-forwards the checkout
and restarts that worker while the server and other jobs continue. Local changes, another branch, a
different origin or failed preparation stop the handoff.
The checkout and selected data remain separate from packaged code: to return to the packaged app,
quit the source app before opening the installed app using the same data. External AI clients do
not participate in this managed session drain; finish their work before switching runtimes.
When running from a Git checkout, frontend changes are detected automatically (no
extra command). CSS updates in place; JavaScript, HTML and other frontend assets
reload the interface after a short settling delay. The current project/tab, open
function, disclosures and main scroll position are restored. Backend workers and
rooms continue running. Edited text or an open dialog/agent launcher delays the
page reload and shows a **Reload interface** button; save or copy edits before
using it. Set `DECOMP_LIVE_RELOAD=0` before launching to disable this behavior.
Packaged apps do not watch files. In a checkout, edits to the permuter worker's Python code restart
only that worker; a turn in progress may be retried. Other Python backend changes require a restart.
Existing running copies need one restart to pick up this file-watching feature.

On a Mac desktop, `DECOMP_TEST_COCOA=1 uv run pytest -q tests/test_macos.py` also exercises a hidden
WKWebView against a fresh app, including authentication, external links and close confirmation.
The homemade N64 demo tests need `brew install llvm lld`, with `$(brew --prefix llvm)/bin` and
`$(brew --prefix lld)/bin` on PATH, plus the free tools from `decomp setup`.


## Start

Type `decomp`, or open decompdecomp from your apps menu. It opens in a window of its own.

1. **Open a game.** Pick a ROM, a disc image or a program file of a game that you own. To try the app first, open
   Demo Quest 64. For an N64, PlayStation or PlayStation 2 game, the app offers to install all of that console's
   compilers first. Then it tests which one built the game. Tick the box only if you accept the proprietary ones.
2. **Start agents.** Any model with an API key (DeepSeek, OpenRouter, OpenAI, a local model), or Claude Code,
   Codex, Cursor Agent, Kimi Code, opencode, Antigravity CLI, ZCode and ZCode CLI. ZCode uses the desktop app's
   session protocol; ZCode CLI uses the separately installed npm CLI. Run several, and different models: they work on different functions at the
   same time.
3. **Watch the map.** Each square is a function. It turns green when its function matches. The autopilot matches
   the easy ones for free, before any agent starts.
4. **Export** (in the project's header). You get a decomp project that builds your game again, byte for byte.

### Cursor Agent

Install the [Cursor CLI](https://cursor.com/docs/cli/overview), run `agent login` in Terminal,
then choose **Cursor Agent** in **Start agents**. The model picker lists the exact IDs
available on your Cursor account. Thinking/effort variants are separate model IDs, so
there is no extra effort control. Your Cursor account's usage limits and billing apply.

Each worker gets private settings and only the app's MCP server; it does not load your
personal MCP servers. On macOS, the existing Keychain login is passed through the child
process environment. Built-in shell, file and web tools are denied: workers use the app's
judged tools. Reported input, output and cache tokens are tracked; missing usage or dollar
costs remain unknown. Live MCP, compile-and-match and permission checks were tested on
macOS; Linux and Windows launch settings have automated coverage.

### Repositories with bundled decompdecomp progress

**Continue a public decomp** recognises a portable `decompdecomp.json` save at the
repository root or under `progress/`, even when the repository also has a
standalone `build.py` and `objdiff.json`. Supply the matching game copy to restore
sources, drafts, names, notes, headers and compiler settings. Saved matches are
verified again; cloning alone does not establish a match. The checkout stays on
disk and its progress folder remains linked to the restored project.
**Open a saved project** accepts the repository root too.

### Connect another AI tool

In **Agents & models → Connect another AI tool with MCP**, choose a project and
create a connection. The panel shows the running local server's port and URL,
project-specific tool descriptions and input schemas, and copyable configuration.
Use **Streamable HTTP** with the supplied **Authorization: Bearer …** header;
configuration field names depend on your client. Clients that only accept stdio
or OAuth connections cannot use this endpoint directly.

Keep decompdecomp running on the same computer. Ask the AI to read `project_info`
and follow the server instructions before claiming work. Each connection can read
and change only its assigned project's work. The credential is shown once; keep it
private. Connections survive app restarts, but a changed server port needs an
updated URL. Revoke access in the same panel. Removing the project also revokes its
manual connections. The external tool controls its own sessions and token costs;
the app does not start, restart or supervise it.

To inspect agent costs, open **What it said and did → Session transcript**. Each new
session keeps the full app prompt (including guides and project instructions), exposed
replies, app tool arguments/results, and reported input/cache/output usage. API helpers
also record each request after context trimming, including tool definitions. CLI provider
system prompts, hidden reasoning, shell output and internal compaction are not available.
**Settings → Database cleanup** previews and clears transcripts from agents already
removed, and optionally from completed sessions before a chosen date (00:00 UTC).
Enable automatic cutoff enforcement and save the rules to check every five minutes
while the app is running, or use **Clean up now**. The existing background coordinator
schedules cleanup without another polling thread and respects the saved maintenance CPU budget. Tasks show progress and can stop
between bounded batches; completed deletions remain. Ongoing sessions and current
agents' resumable conversations are kept. Empty automatic checks do not create jobs.

**Remove agent** deletes that agent's recorded app transcripts and saved API conversation.
Its saved matches, candidates, notes, attribution and lifetime usage/cost totals remain.
Export transcripts before removing an agent if you want to keep them. Deletion makes
SQLite pages reusable; reducing the database file itself requires separate compaction.

The transcript is local, paged separately from the short live feed, survives restarts,
and redacts known credentials. It contains source and can grow with long sessions;
older sessions recorded before this feature cannot be reconstructed. Token counts and
dollar costs are shown only when reported, not estimated or assumed to be zero.

**Agents → Repair examples** collects evidence for improving autopilot, the permuter,
and other deterministic repairs. New verified matches save the winning candidate,
the closest compiled failed attempt (when present), available headers, configured
compiler settings and judge provenance. Agents can include `repair_note`,
`repair_category` and `repair_automation` with a single-function `compile_and_diff`
request; these notes survive only if that candidate produces a new verified match.
`report_repair` can add an explanation afterwards. No additional model call is
needed when the note travels with the candidate.

The default **Comparable source repairs only** filter requires changed source and comparable captured build settings. Equivalent captures for the same function, target, successful source, settings and review state are grouped, with their count shown; the underlying evidence remains intact. **Metrics coverage** identifies cumulative policy/deep-history totals and collection windows by trial origin, so different cohorts are not mistaken for comparable success rates.

Filter by category and review state, browse one or all projects, add review notes,
and download summaries or full examples as local JSON. Full exports include code,
assembly and available headers; summaries omit those fields and the code diff,
although notes can still quote code. Nothing uploads automatically or becomes a
live rule. The comparison is against the best saved failed attempt, which can
involve other edits, another agent, or changed settings: explanations are hypotheses,
not proof of which individual edit caused the match. First-try matches can be shown
by clearing **Only with a failed candidate**. Imported-project examples record
objdiff verification, symbols and source paths, but do not pretend to capture the
build system's per-file flags/includes. Old compiler settings are unknown when
not captured at the attempt. Existing attempt/context rows are reused to avoid
duplicating large source and header snapshots. Recording starts with new matches
after this update; historical score-zero attempts are not retroactively labelled
as verified matches.

You can also continue a community decomp: paste the address of its repository and pick your own copy of its game.

Some community projects do not provide `objdiff.json`. After you supply the matching game and the project's build
succeeds, decompdecomp can generate it for simple N64, PS1 and PS2 splat projects. It requires a SHA-1 in the splat
config, built C objects and one unstripped ELF beside a rebuilt image with the same SHA-1. The build tools still
need to work on your computer; this does not port a project's build system.

References come from a separate all-assembly split of your verified game. Each reference's linked code and data
must reproduce the game's original bytes. Candidate C objects are never copied into the reference set. Generated
files stay under the private `.decomp-wtf/objdiff-references/` folder. Existing `objdiff.json` and `expected/` stay
untouched. Custom layouts, overlays and compressed code need manual setup; the app reports the missing requirement.


Already have repositories on disk? Open **Continue a community decomp**, then **Import checked-out projects**.
Choose their parent folder and click **Scan folder**. It looks through platform subfolders and lets you select
projects to import. Review their contribution rules first. By default, clone separate copies containing only
committed files. You can instead use existing checkouts to keep game files, tools and uncommitted work. Those stay on their
current branch; later, agents commit accepted changes there. Nothing is pushed automatically.

Importing only adds projects to the app. Open each one to review setup and measure progress before starting agents.
Unsupported build systems still need manual setup from the project's README. A ROM project's saved `progress/`
folder uses **Open a saved project** instead. The scan stops at each checkout, ignores hidden/build/tool folders,
and reports when a size or depth limit means you should scan a smaller folder.


When you close the window while agents work, decompdecomp asks you: stop them and quit, or keep them working.
**Quit** ends the app and everything it started.

## Coming from decomp.wtf or an older version (1.0.x)

Your projects stay, decomp.wtf's too: decompdecomp continues decomp.wtf and reads the same folder and database. On
Linux, the new version opens the same `~/Decomp` folder, with every match, name and note in it.
The old Windows installer kept its projects inside a WSL distribution. The Windows app now runs natively and uses
`%USERPROFILE%\Decomp`; it uses WSL for some Linux-only compilers, but doesn't scan WSL for projects automatically.

To move projects from the old Windows install, close its app, then copy the whole `Decomp` folder from
`\\wsl.localhost\decomp-wtf\home\decomp\Decomp` to `%USERPROFILE%\Decomp`. Include the hidden `.decomp` folder
inside it: that holds the project database and saved matches. The app reconnects projects to their copied folders.
Did you keep a project outside the `Decomp` folder, in `Documents` for example? Move that project's folder into
`%USERPROFILE%\Decomp` too, and keep its folder name: the app finds a moved project by its name there.

**Open a saved project** is for something else: a `progress/` folder that decompdecomp saved, with a
`decompdecomp.json` in it. Use it if you only have a project's `progress/` folder, with your own copy of the game.
A project's own folder isn't a save, and neither is its `.decomp.wtf.json`: that file says how the project builds.
Keep that file's name as it is. decompdecomp still reads it as `.decomp.wtf.json`.

1. **Install the new version** the same way as before: the install command above or the latest `.deb`, `.rpm`,
   AppImage or Windows setup bundle. The installer removes an older `.deb` or `.rpm` of decompdecomp, so your apps menu shows
   one. Going the other way, from the install command to a package? Remove the old copy first: `uv tool uninstall decompnet`.
2. **Start it** with `decomp` or from your apps menu. It opens in its own window now, not in your browser.
3. **Start your agents again.** Helper agents that were working in the old version don't restart by themselves the
   first time. Open each project and start them; from then on they restart by themselves.

What changed on the way:

- **Managed agents run inside the app.** `decomp connect` and `decomp mcp` are gone. On its first start, the new
  version removes the old `decomp` entries from your agents' settings (Claude Code, Codex, opencode, Antigravity and
  others), and its home page says what it removed. Before that, those entries started the old app again whenever
  you closed it.
- **The Source tab is now the Export button** in the project's header.
- **Windows projects and compilers.** The app server and window run natively on Windows. Linux-only period compilers
  run through WSL 2, using temporary Linux-side build stages so old 32-bit tools work. Run `decomp shortcut` once to
  put native shortcuts on the Windows desktop and in Start. Setup installs WebView2 and .NET Framework 4.8 when missing;
  pywebview is installed with the app.

## Moving to another computer

decompdecomp saves the work on each N64, PlayStation and PlayStation 2 project as plain files in the project's `progress/`
folder as work changes: matched C, best attempts, headers, names, notes and settings. It never contains the game,
so you can keep it in a git repository. You can edit these files or pull another contributor's changes into
`progress/`; decompdecomp reads them on the next project action and checks the saved functions again. GameCube, Wii
and PC projects and community decomps are git repositories already: push them.

The simplest way is to copy your whole `~/Decomp` folder to the same place on the new computer. Copy the hidden
`.decomp` folder in it too: it holds the matches of every project. decompdecomp then finds every project. To take a
single N64, PlayStation or PlayStation 2 project, or to keep it in git:

**Share or commit only `progress/`.** The rest of a project's folder is made from your copy of the game, and the
game's code is its rights holder's: keep it to yourself.

```
~/Decomp/<project>/
├── progress/         your work: share this, commit this
│   ├── src/              each matched function's C
│   ├── best/             the closest attempt at each function that doesn't match yet
│   ├── include/          the shared headers every function is compiled with
│   ├── decompdecomp.json the game's fingerprint (never the game), compiler, layout, names, notes, settings
│   └── README.md         what this is and how to continue
├── rom.z64           your copy of the game: never share it
├── asm/  assets/     taken out of the game: never share them
└── src/  include/  *.ld  *.yaml  *.txt   the build decompdecomp makes from the game, again on any computer
```

1. Copy the project's `progress/` folder to the new computer, or push it to a git repository.
2. On the new computer, in decompdecomp: **Open a saved project**. Pick the folder (or paste the repository's
   address) and your own copy of the game.
3. decompdecomp checks it's the same game, rebuilds the project, and judges every saved function again.

## Platforms

| Platform | Status |
| :-- | :-- |
| Nintendo 64 | Recommended |
| PlayStation (PS1) | Beta |
| PlayStation 2 | Beta |
| PlayStation 3 | Beta (from your decrypted EBOOT.ELF; the free ps3dev GCC, or your own SDK's compiler) |
| PSP | Beta |
| GameCube, Wii | Beta |
| Windows PC games | Beta |
| Nintendo DS | Beta |
| Game Boy Advance | Beta |
| Original Xbox | Beta (from your default.xbe, .xiso or Redump .iso; needs Visual C++ 7.0/7.1 and Ghidra; wine for games built with link-time code generation; your own XDK, optional, sets its library code aside) |
| 3DS, Switch, Xbox 360 | Community decomps only |

1.0.3 rewrote most of the app, so every platform needs testing again, Nintendo 64 too. Try it on a game you own and
[tell us how it went](https://github.com/n-delic/decompdecomp/issues): what worked, what didn't, what was confusing. Reports are how a platform goes
from beta to recommended.

Proprietary compilers (CodeWarrior, Visual C++, IDO) are installed only after you accept their licenses in the app.
Visual C++ 2017 runs through [wine](https://www.winehq.org/) on Linux and macOS, which you install yourself (your distribution's `wine`
package; on a Mac, Wine Stable from [Gcenx's builds](https://github.com/Gcenx/macOS_Wine_builds/releases), which needs Rosetta 2 on Apple Silicon): the app says
so when you install that pack. The older Visual C++ run without it. A game built with a Visual C++ the packs don't
have (2012, 2013, 2019, 2022...) builds with your own copy of it: point Setup at your Visual Studio's VC folder (it's
read where it is, never copied).

## Questions people ask

**Is this just Ghidra?** No. Ghidra (and IDA) show you C so you can *read* a program. That C usually doesn't compile,
and when it does, the game's compiler makes different machine code from it. decompdecomp keeps a function only when
its C, built by the game's own compiler, gives exactly the original bytes. That's what lets the result build the
game again, the base for ports and mods.

**What does the C look like?** At first, raw. A match proves the code is right, not that it's easy to read. Here is
one function of Demo Quest 64 as the autopilot matched it, and the same function made readable. Both compile to
the same bytes:

```c
/* As matched */
void func_80000A0C(s32 arg0) {
    s32 temp_v0;

    temp_v0 = D_8004AE30 + (arg0 * (D_8004AE58 + 1));
    D_8004AE30 = temp_v0;
    if (temp_v0 > 0xF423F) {
        D_8004AE30 = 0xF423F;
    }
}
```

```c
/* Made readable: adds points for a hit, times the combo, up to the highest score. */
void add_score(s32 points) {
    g_score += points * (g_player.combo + 1);
    if (g_score > MAX_SCORE) {
        g_score = MAX_SCORE;
    }
}
```

Agents do that second step too: once half a game matches, **Start agents** offers **Polish matched code**. Helpers
name functions, globals and parameters, describe structs in the shared header and add a comment, and a change is
kept only when the function still matches byte for byte. Names are the agent's reading of the code: the bytes
prove what a function does, not what its author called it.

**Has a whole game been decompiled with it?** Not yet. Games get far (hundreds or thousands of matched functions),
and the export rebuilds the game byte for byte from matched C plus assembly for what isn't matched yet. A game at
100%, polished, is the goal, and the first one will be announced here.

**How do I commit progress to my standalone repository?** In project Settings, use **Save your progress → Sync to Git repository** and choose the checkout root of an existing decompdecomp `build.json`/`build.py` export. Commit or move its existing changes first, then click **Verify and sync**. The app checks additions in a private copy and requires the complete rebuilt image to equal your original before publishing source, isolated headers, per-function compiler settings and progress metadata. It lists the files ready to commit and explains which app matches could not be integrated. Existing hand-maintained C and build scripts stay intact; later syncs update only their own files, and committed manual edits take those sources out of sync ownership. No commit or push happens automatically. Ordinary `progress/` saves remain separate. Imported projects already keep their source in their own checkout; custom export layouts need an adapter.

**What do I end up with?** The source code of a game you own, that builds it again. A DS game started from scratch
reached 2,400 matched functions (58% of its code) in under two days, about half of them matched by free models, and
its project built a game that boots in an emulator: matched functions from their C, the rest from the original's code
until agents get to it. It isn't overnight: the last functions are the big ones, and they take time and tokens. But
every match stays matched, and each one makes the project more of what a game's source is: something you can read,
change, port and mod.

## Help make it better

decompdecomp is built in the open, and it's meant to be easy to work on. Read [CONTRIBUTING.md](CONTRIBUTING.md)
first. A copy of decompdecomp that runs from a git checkout is a separate app, with its own folder, so it never
touches the projects of the decompdecomp you have installed.

- [The guide](docs/GUIDE.md): how it works, agents, teams, platforms and the export.
- [ROADMAP.md](ROADMAP.md): what's planned, and which feature works on which platform.
- [Issues](https://github.com/n-delic/decompdecomp/issues): problems and requests.
- [Discord](https://discord.gg/hhEm3zZagB): questions, progress on your games, and talking with the people building it.

## Use it responsibly

- Use only games that you own. decompdecomp contains no game code and supplies no ROMs.
- Everything runs on your computer. Nothing of your game is sent anywhere, except to the AI model you choose.
- Do not publish the code that it makes from a game. That code belongs to the rights holder of the game.
- Follow the rules of any project that you contribute to.

## Credits

decompdecomp continues [decomp.wtf](https://github.com/i12bp8/decomp.wtf) by
[Sam Wolfs (@i12bp8)](https://github.com/i12bp8), who made it up to version 0.10.6 and then archived it. Thank you,
Sam. decompdecomp is not related to decomp.wtf, and its author does not endorse it.

decompdecomp uses splat, spimdisasm, objdiff, decomp-toolkit, m2c, Ghidra, decomp-permuter, wibo, wine and the decompals
compilers. The
[guide](docs/GUIDE.md#built-on) lists them.

## License

decompdecomp is free software under the [GNU Affero General Public License version 3](LICENSE) © 2026 NDelic.
Anyone who gives or sells a copy of it, changed or not, must give its full source under the same license. So must
anyone who runs a changed copy for others to use over a network, a hosted room for example.

The code that came from decomp.wtf was released under the MIT license by Sam Wolfs; its notice is in
[LICENSE-decomp.wtf](LICENSE-decomp.wtf).

<p align="center"><sub>Not related to Nintendo, Sony or Microsoft. Platform names are trademarks of their owners.</sub></p>

### RNC-packed N64 programs

The N64 importer recognizes RNC method-1 blocks, verifies both CRC16 checksums, and checks the decoded bytes for
code before adding them to the analysis image. Method 2 and encrypted/locked streams are not supported. A small
initial function count can still mean that the game uses another packing format or an undiscovered loader.

For RNC-expanded exports, `baserom.z64` is the expanded analysis image and `baserom.cartridge.z64` is the original
normalized cartridge. Keep both private. The build first checks the linked analysis image, then reconstructs the
packed blocks using literals from the newly linked output and the original stream's coding decisions. It rejects
changes incompatible with those references and checks the complete cartridge against the original. This template
operation supports matching builds; it is not a general compressor for mods. Other expanded packing formats fail
export with an explanation until they have a verified cartridge reconstruction.

An identical rebuild can still contain assembly and binary fallbacks. Check the reported number of functions
selected as C separately; an identical cartridge does not mean that every function has been decompiled.

For imported overlays missing their original strings or tables, see [supplemental original overlay data](docs/OVERLAY_DATA.md).

Verified full-image imports include linked assembly routines as target-only units: assembly bytes never count
as recovered C. Assembly wrappers containing whole binary programs are not treated as single functions; an
explicit coverage notice identifies their exclusion from function progress.

### Repair preflight and reuse discovery

Saved ROM drafts get an advisory warning preflight in agent briefs and deep-search output when their pinned
GCC supports it. It uses separate warning flags and caches diagnostics for unchanged compiler, header and
source inputs. An uninitialized-variable warning is a reason to check the draft before tuning it; warnings
never change project settings or count as a match. Imported projects retain diagnostics from their own build;
extra diagnostic builds for those projects and other compiler families are not supported yet.

Generated local repairs skip direct calls that disagree with an unambiguous plain C prototype. Macros,
includes, indirect calls, unspecified argument lists, variable arguments and C++ remain the compiler's job.
The normal compiler and exact judge still decide every accepted improvement.

Template/address/SDK discovery remembers its last visited function, including interrupted passes. Cached
misses are read together per function, and this phase takes at most half the remaining reuse time so wider
cross-project discovery can run too. Both phases retain the shared compile budget; a cursor does not skip
new candidates permanently.

### Permuter budgets

Local deep searches can work on saved, scored C while an agent owns the function. The agent keeps
its claim and can continue submitting candidates; only better scores replace the shared best attempt.
A verified exact match completes the function normally. Imported-project trials serialize through
the normal build locks and restore unfinished source. Compiler/private-header preparation is deferred
while another agent holds the function. One deep search per function runs at a time. These rules also
apply on a room host; room guests retain the host-lease rules below.

Room members can run deep passes too. Each active function gets a host claim that
is renewed during the search and released when the turn stops. ROM-room clients
compile candidates locally and share improvements; exact matches must also pass
the host's judge before they are accepted locally. For imported-project rooms,
the host builds and judges each draft, so this does not offload compilation from
the host. Compiler-setting changes remain on the host. A disconnected or refusing
host stops the affected search; its claims can expire normally if release cannot
reach it. No AI tokens are used by these searches.

For a longer search of one function, open its drawer and expand **Deep permuter · this function**.
Choose a time limit and maximum candidate compiles, then select **Run deep permuter**.
For local ROM projects, **Compiler profiles per candidate** selects how many profiles each local/random
C variant is tried against: **1** uses the project default; **2** adds the distinct alternative used by
the most matched functions. Higher counts add further profiles in that order, including differences in
flags. Profiles with no matches are excluded. The same setting is available on the project-wide pass.
Each source/profile test uses the shared compile allowance, so increasing this count tests fewer distinct
C variants within the same budget. Exact alternatives reserve verification allowance and go through the
existing function-only source/header/profile check, respecting automatic compiler changes. Alternative
near-miss scores are retained as diagnostic evidence, without replacing the saved current-profile score.
Changing the profile count makes completed project-pass entries eligible again. Imported projects and
rooms keep their build/host compiler settings; multiple-profile searches are not available there.

The scorecard shows the freshly measured starting score and percentage, the current best, and how many
verified score reductions this run found. Local repairs and random rewrites share that improvement count.
Expand **Technical output** for compiler diagnostics; the progress bar stays visible when output is closed. **Stop** ends the job
after its current compiler/generator finishes. Better candidates and exact matches are saved immediately.
This bypasses the background queue’s instruction and turn limits for the selected function only. It visits the
current local candidate list, then uses random rewrites; it cannot exhaust every possible C program. No AI tokens
are used. Cached misses are skipped on later deep runs when their build inputs still agree.


In **Settings → Permuter limits**, raise the maximum instructions per function to
include larger near misses. Defaults are 400 instructions, 60 seconds per turn and
four turns per function. All three limits are global, saved across restarts and
apply to new turns in both ROM and imported projects. Raising the turn limit gives
previously exhausted functions more turns. Larger functions compile more slowly;
the permuter still selects candidates within 6% of an exact match. Deterministic
repair passes retain their separate budgets. Idle workers check again within about
three minutes; a finished permuter starts again when project agents start.

### Reset an agent's card metrics

Choose **Reset metrics** on an agent card to start its displayed matches, improvements,
compiles, reported tokens/cost and session counters from zero. This keeps verified code,
attribution, transcripts and project/model lifetime totals, and does not stop the agent.
The baseline survives app restarts. The card shows total reported tokens and time since
its last improvement; a reset clears that improvement timer until new progress arrives.
Its runtime still measures time since the latest start. Provider usage reported after
reset belongs to the new period, even when the request began earlier; missing usage
is not reconstructed. Matched/improved function links follow the same reset period.

### Choose functions by recency or model history

The Functions list shows sortable **Last improved** and **Last checked** ages, with
exact local timestamps on hover. Checked means a recorded candidate submission,
including compile errors. Improved means a compiled candidate beat every previous
compiled score; ties and worse candidates do not reset the clock. Missing history
shows **Never**; old work without saved attempts cannot be reconstructed.

In an agent's **Work settings**, choose **Oldest checked first** to take functions
with no checks before the least recently checked ones. **Only functions never
checked by this model** is a separate filter that can be combined with any order.
It uses recorded session model IDs across workers and restarts, ignores `#effort`
suffixes, and includes failed candidate submissions. It does not infer model aliases
or count claims without submissions. Choose an explicit model; missing historical
model attribution cannot be reconstructed. The filter also applies to named claims
and never falls back to previously checked functions. Existing claims stay held.

### Background verification and stuck compilers

Saved-progress verification runs one project at a time. The sidebar and read-only project views show the current database while checks continue; the verification banner shows completed/total functions and lets you pause or resume. Pausing preserves the saved files and prevents project edits or judging from bypassing unfinished verification.

Native compiler commands have a wall-time ceiling of 300 seconds (or their shorter command timeout). Set `DECOMP_COMPILER_TIMEOUT_SECONDS` before starting the app to change that ceiling. Imported builds also enforce their build timeout while waiting for output, including silent compilers. Timeout and cancellation stop the compiler tree; on macOS and Linux the app also stops remaining members of its isolated process group when a driver exits. On startup, recorded orphan groups are cleaned up only when process birth identities match and their previous app owner is gone. The tracker samples active groups once per second; unrecorded processes are never killed by startup cleanup. Windows uses tree termination during a run; startup orphan-group cleanup is currently POSIX-only.

### Compiler memory on macOS

Native compiler and imported-project build processes are monitored while decompdecomp runs. If a process in a build tree exceeds 2 GiB of resident memory, the build is stopped and reports the limit. This is a sampled watchdog, so short spikes can exceed the limit. To change it for apps launched from Finder, run `launchctl setenv DECOMP_COMPILER_MEMORY_MB 1024` in Terminal and restart decompdecomp; the value is a limit in MiB (at least 16). Use `launchctl unsetenv DECOMP_COMPILER_MEMORY_MB` and restart to restore the default. The limit applies to each process; reduce deep permuter threads and agent concurrency to limit combined memory use. Compilers inside Linux VMs or containers have their own limits.

Compiler-specific matching observations, measured cases and failed experiments are kept in
[the compiler matching learning log](docs/COMPILER_MATCHING_LEARNINGS.md). New repairs propose source
alternatives; only the ordinary exact judge can accept them.

Automatic search also learns which repair families improve results under the same compiler profile and
mismatch kind. It keeps an exploration lane and waits for enough observations before changing priorities.
The repair examples dialog's **Classify this project** button groups untouched new evidence, retains
existing reviews, and archives duplicate captures. Reviewed evidence remains available through the review
state filter; classification does not enable a new compiler rule.

Agent local repairs use the shared copy, typed-address, scope and inferred-layout candidates, with measured
repair priorities. Machine-generated batches check their starting C first: a broken baseline returns one
scoped diagnostic instead of compiling six equally broken variants. Explicit patches can still repair that C.

For offline regression work, export **full repair examples** and run
`uv run python -m decompnet.app.repair_replay repair-examples.json --output replay-results.json`.
This uses already-installed pinned ROM compilers, checks the captured baseline/control, and fairly rotates
repair families under per-case trial/time limits. It reports recovered compiler output, improvements,
neutral/regressed trials and errors without updating game progress. Imported builds and missing historical
inputs are explicitly skipped. See [the replay guide](docs/repair-replay.md) for scope and options.

Use **Agents → Repair examples → Test automatic repairs** for examples from up to 20 distinct functions matching the current
filters, or test a single expanded example. Trial/time controls, progress, results and **Stop replay**
are in the dialog. Replays do not apply source or change review states. Source hashes distinguish
direct random-rewrite evidence from inherited cascade labels before suggesting a new deterministic rule.
Compact project facts summarise compiler ranges; each claim retains its actual compiler and user instructions.
Repeated unchanged claims return a short status, and oversized/sparse batch requests explain why only one
function was returned.

Targeted local repair searches include arithmetic operand-order experiments and, on MIPS targets with direct-call/register evidence, forwarding declared incoming parameters into locally prototyped calls. These are bounded hypotheses, not semantic guarantees: only the ordinary linked-byte/objdiff judge can accept a match. Compiler/converter infrastructure failures are reported separately without consuming a failed-C attempt.

### Sharing code between local projects

**Matches from other games → Run now** searches ROM-mode and imported projects with the same processor and
byte order, including assembly-only targets and enabled local SDK signatures. The panel distinguishes detected
shared code, candidates ready to test, and functions needing writable C scaffolding. Detection is not recovered C.
Function details group name suggestions from both kinds of projects and SDKs.

Verified C can move in either direction, including between imported projects. The receiving project's compiler
and ordinary byte/objdiff judge decide; donor compiler settings do not exclude an exact-code lead. Unsupported
source-less builds show a blocker. Supported existing C object rules are probed before adding candidate files;
the original assembly and reference objects remain intact. When automatic reuse is enabled, new matches queue
compatible projects under the existing shared time/attempt budget. Discovery is refreshed by free passes rather
than repeated on every UI poll; wider similarity and SDK-source/template searches remain ROM-only.

### More local repairs before another model turn

Autopilot can reuse a verified small MIPS wrapper from the same ROM project when its instruction
shape and callee agree and only one paired global address differs. This includes low segment addresses.
It tries up to three donors through the destination's normal compiler and byte judge; failures are cached.

Shared draft/permuter and agent local repairs also try holding an integer global's address across a call,
and splitting a combined pair of bitmask clears into ordered compound stores. These are source hypotheses:
only the ordinary linked-byte or objdiff judge accepts a match.

In **Settings → Local repairs between agent turns**, optionally enable an automatic local pass after a
single-function `compile_and_diff` or `compile_patch` near miss (within 6% by the instruction score).
It is off by default. The defaults are two generated trials and five seconds per submission; both are
configurable. Trials share the agent's existing compile/stall limit, reuse scoped attempt caches, and
make no model requests. Baseline verification can require an additional diagnostic compile. The time
limit is checked between operations; an active compiler/build finishes under its own timeout.

The tool response identifies the local pass, shows any selected changes and returns the current candidate
revision. Failed alternatives do not replace a better starting candidate. Successful matches still go
through the normal judge, and repair examples retain the automatic pass's family and source hashes.
The pass supports ROM and writable imported projects through the shared local-repair runner; it does
not change project compiler settings.

### Agent continuation and repair evidence

Claim packets diagnostically check inherited candidates before offering generated experiments. When an
undeclared name has one consistent declaration in that function's scored history or current context,
decompdecomp can prepend it candidate-locally and verify the result. Ambiguous types and failed recoveries
remain explicit blockers; shared headers, saved C and match counts are unchanged. Checks are cached by
claim/build/source scope; a five-second batch allowance is checked between builds, and active builds retain
their normal timeout.

Session transcripts now retain structured handoffs with remaining claims, recent scores, recorded notes,
experiment history and the last exposed reply. A later session receives this as historical data and must
refresh its candidate/claim state. Credit-required model failures pause with guidance to add credits or
choose another model.
Function notes record measured before/after scores, including zero change. Deep permuter summaries
carry these scores through room sync; older notes recover comparisons when measured history exists.
Notes written before any measured baseline remain explicitly unscored.

Repair examples offer target-function, supporting-source and full-source diff views. Pattern candidates
identify edits reproducible by bounded repair families; supporting declarations can still be essential.
These hints do not classify, promote or apply examples automatically. Existing isolated replay remains the
verification path. New hypotheses cover shared return tails, simple compound stores, redundant counted-loop
guards, candidate-local float argument prototypes/constants, and explicitly named scalar runtime helpers.

### Library leads and historical scores

Local SDK object imports now retain instruction-shape fingerprints. Similar library routines can
appear as **unverified** leads alongside exact signatures, and explicitly imported local C can be
tried through the normal compiler and byte judge. Rescan existing SDK folders once to build the
shape index. Unconditional macro dependencies are retained with imported C; no source is downloaded.

Local repairs, Autopilot and the deep permuter can try bounded formatter source shapes and target
jump-table case order. Small combinations cover interacting declaration and initialization choices.
Only a verified destination match counts as progress. In a ROM function's inspector, **Historical
score · recheck in Diff** indicates changed or missing compiler/header/scoring evidence; Diff shows
the saved C compiled with the current inputs.

### Target-guided local repairs

Autopilot, agent local experiments and the deep permuter can try candidate-local call-argument,
address-alias, single-field layout, boolean-mask and temporary-lifetime repairs. MIPS targets
also guide field-load order. These bounded compiler trials use no model tokens. Run the deep
permuter on one near-matching function when several repairs may need to interact; its existing
time and variant limits still apply. The ordinary byte/object judge is the only acceptance path.

Agent **Next function** options include descriptions in the dropdown and a live explanation below it.
**No compiling C yet** selects only eligible open functions without a recorded successful C compile or
scored candidate. It includes never-tried functions and compile failures, and stops when none remain;
it does not fall back to already-compiling functions. Existing filters and claim ownership still apply.
Historical evidence is used; it does not rebuild every function to check whether its C compiles today.

Repair evidence now suggests a review category using the same conservative triage as **Classify new**;
existing categories are retained and suggestions save only through the review controls. Function-only pattern
hints also recognise captured cast/bitwise, dead unsigned shift/store, and explicit floating-cast lifetime
repairs. These bounded hypotheses run in shared draft, local and deep search, with the ordinary destination
judge; volatile code and unsupported forms remain untouched.

### Recent-match follow-ups

Repair examples separate **Build activity** from isolated source-edit cards. Build captures are grouped by
project/unit, captured settings/context and one-minute window; each card opens the latest representative.
This is a display grouping, not proof that records came from one build. Individual records remain stored
and exportable.

Shared local/draft/deep repair search can try an unused same-width integer local for a copied-value zero
test, or stage an explicitly declared pointer-returning call through an unused pointer local. These remain
bounded source hypotheses and must pass the ordinary destination judge.

Reference preparation retains reachable same-file and multiline macro definitions verbatim, including their
dependencies. It does not replace original graphics-style macro invocations with guessed expansions, and
conflicting definitions ask for the correct source/header pair. Reference aggregate parameters and complete
save/restore snapshots produce ABI/layout diagnostics; no aggregate type or size is invented.

### Local browser workers (initial bridge)

Settings → **Pair a browser worker** creates a five-minute, single-use code for one exact browser origin
and one local KMC GCC 2.7.2 ROM project. Enter it in N64 Browser Lab at that origin. The browser receives
a separate one-hour capability, not the window secret or an unrestricted agent token. Revoke project
browser access in Settings; restarting the app invalidates all pairings.

The browser can manually list unclaimed open functions, fetch one task, and submit plain C. Tasks expire
after five minutes and can be submitted once. No claim is held while editing. The host rejects claimed
or stale work and recompiles with its own settings and unchanged byte judge before recording progress.
Revocation and context/target/settings changes are checked again inside the recording transaction.

Initial scope: local ROM mode, self-contained declarations, KMC MIPS III 32-bit profiles, limited compiler
flags (including debug levels). No includes, inline assembly or attributes in submitted C, arbitrary
commands, files, automatic claims or room joins. Imported object projects need a separate adapter.
Browser localhost permission/CORS failures should be resolved by pairing the exact displayed origin;
there is no wildcard CORS enablement for existing private APIs.

### Choose a fork for app updates

In **Settings → About → Update from a GitHub fork**, enter the public GitHub
repository URL and branch, then select **Save update source**. The app checks the
source before saving it; invalid or unreachable forks leave the old source intact.
Explicitly choosing `main` follows that branch's commits. Saving does not install
code or restart the app. **Check now** reviews the available update; if automatic
updates are enabled, future checks may install from the chosen fork automatically.
Only select a fork you trust, since updating runs its code. Packaged editions keep
their existing platform update restrictions. This changes the app's update source,
not any imported game's repository.

### Reuse verified C by function name

Autopilot also tries meaningfully named, matched functions from compatible imported games,
including ROM → repository, repository → ROM and repository → repository leads. This can reuse
SDK C even when compiler differences prevent a byte fingerprint match. Run Autopilot with both
projects imported; enabled cross-project cascades also try these leads after new matches.

Names select hypotheses, never matches. Imported C brings a bounded local header closure through
the destination's pinned preprocessor where supported; imported destinations keep their own build
headers. One explicit numeric version boundary from the source may be tried alongside its defaults.
Each candidate and a few deterministic repairs use the ordinary destination judge, with no AI calls.
Unsuccessful trials are cached by their actual inputs, projects rotate fairly, and attributed results
appear in the existing matches-from-other-games history. Better ROM drafts retain private declarations;
only an exact destination verdict counts as a match. Assembly-only units and unsupported preprocessing
or foreign helper/data definitions still need their existing setup/source-preparation adapters.

### Export deep-permuter metrics

In the deep-pass settings, expand **Rewrite performance** and select
**Export metrics (CSV)**. The app saves it in Downloads and displays its full path
(or an actionable error), including in embedded desktop windows. The CSV contains one row per rewrite family across all
search phases, in the table's selected sort order. It includes project slug, game
platform, client platform, app version/build and UTC export time, plus totals,
detailed measured outcomes and timing coverage. It contains no source code, ROM
bytes or credentials. Missing older measurements are blank; `partial` timing must
be compared using `measured_attempts` and measured gains, rather than all-time
counts. Totals and newer outcome measurements overlap and must not be added.

### Experiment with agent prompts

**Agents & models → Agent prompts** edits the global defaults. Each agent’s **Prompts**
button can override its initial prompt and app instructions separately; unchecked fields
inherit the global defaults. Global initial templates are separate for ROM work, imported
projects and polishing. Use the displayed placeholders, and double literal braces as
`{{` / `}}` in initial-prompt templates. App instructions are literal text.

Save prompts applies changes to new sessions. Restore built-in defaults removes global overrides;
individual agents can return to the global defaults. Project instructions,
guides and handoff context are still appended to the initial prompt. App instructions are
decompdecomp’s MCP/API instructions, not the CLI provider’s private system prompt.
Effective session text is recorded in transcripts; prompt profiles distinguish efficiency cohorts.
### Agent tools and usage

In **Agents & models → Agent tools & usage**, optional tools can be disabled globally.
Tools are sorted by recorded use, with the least used first. Refresh the counts to
include recent calls. Core project, claim, function, compile and release tools stay enabled.
Each agent also has a **Tools** button to disable additional tools for that agent.
Disabled definitions are omitted from MCP tool discovery and API model schemas;
new sessions or MCP reconnects refresh the context, while cached disabled calls are rejected immediately.
Counters are local and survive app restarts and agent removal. They include surviving
saved transcript requests plus future recorded calls; older unrecorded calls cannot
be reconstructed. Zero means zero recorded uses, not proof that a tool was never used.
The same expandable tool descriptions and parameter cards appear in transcripts and manual MCP setup.

Browser workers receive the host's supported KMC flag variations with each function task. Switching a browser variation is local until submission. The host accepts only a variation offered by that task, recompiles it independently, and publishes a function-only profile/source bundle only after an exact ROM-byte verdict and a final authorization/freshness check. Other compiler families still require browser WASM toolchains.

### Session startup and reusable knowledge

Matching sessions receive compact project facts and one coordinated function claim in
their initial prompt. They can start compiling without first asking `project_info`
and `claim_function`. The same selection rules and room coordinator apply; long briefs
are abbreviated with instructions to fetch omitted evidence. This removes initial tool
round trips, but the supplied evidence still occupies model context. Polishing gets
project facts and keeps its separate source-file claim workflow.

Transcripts label the app’s MCP probe **Connection check (app only)** and the actual
tool catalog **Tools exposed to agent**. New probe calls do not count as agent tool use;
earlier stored counters remain historical.

**What agents learned** is for reusable matching techniques across a game/compiler,
rather than work logs about one function. Use function notes for those. The user can
**Accept for all agents** with one supporting model; automatic acceptance still needs
two different models. Accepted knowledge is identified as user-approved or model
consensus, without claiming a single-model tip was independently confirmed.
### C lines behind assembly differences

In a function’s **Edit** tab, red lines are associated with differing, extra or moved
compiled instructions; yellow lines have operand differences. Click a marked line
number to see the target and compiled instructions. Editing the source clears these
marks until another comparison. Agent compile responses include bounded C-line evidence too.

These are compiler debug locations from the exact compiled object. Optimization can
associate several statements with one location, so the marks are advisory. No compiler
flags are changed to manufacture a map. Missing target-only instructions have no
compiled C location. ELF DWARF 1/2+ and ECOFF are supported; absent, ambiguous or
unsupported information is labelled unavailable, including COFF/CodeView for now.

### Investigate near misses before broad permutation

Configured local source preparation can test a private copy of a small original range-predicate
helper and bounded same-width signed/unsigned mask declarations. Actual reference bodies, prototypes,
constants and scope are retained; these are hypotheses, not inferred semantics. Imported projects
keep their own build headers, so private-header predicate/field changes need an adapter there.

When only two loads differ in order, local repair ranking prioritizes load, declaration and scheduling
families. A candidate-only restore/return epilogue triggers a boundary warning: inspect the next
original padding entry before spending more model tokens. Nothing trims candidate bytes or changes a
boundary automatically. Prepared-source evidence now highlights differing comparisons and compiled
immediates, and briefs summarize unsuccessful probes for the current saved source/build scope.

N64 reimport can recover large functions whose first instructions were caught at a
scanner boundary while the remaining body was left as binary data. Recovery is bounded
and inferred; the normal linked-byte judge still decides matches. Existing matches and
naming provenance survive when their complete targets are unchanged.

**Local repairs:** use the Deep permutation pass to try targeted repairs on saved C,
followed by random rewrites. The separate near-match pass is no longer shown.
**Recover saved matches**, below Autopilot, rechecks historical successful C/header
combinations against the current project. Its time and compile limits apply to the
whole recovery run; every recovered match must pass fresh verification. No AI tokens
are used.

ROM code audits also show possible function-pointer references and unowned bytes inside
mapped segments. These are review leads. N64 reimport can recover narrowly proven
whole gaps without losing verified sources or their private headers. Repository sync
can recheck historical exact headers and isolate plain helper definitions; new matching
exports include a build-time verified-entry helper for scattered constant pools.
See [the audit guide](docs/ROM_CODE_AUDIT.md).

Code audit results and evidence can be reviewed in Settings, with saved confirmation,
dismissal and investigation states. Reviews never adopt code or pause functions;
changed ROM/layout results require a fresh audit. Export mapping errors identify the
missing constant ranges and sizes. See [the audit guide](docs/ROM_CODE_AUDIT.md).

MIPS code audits recognize a bounded guarded jump-table pattern and trace local
pointer loads into indirect calls. Reviewed table mappings allow every case path to
be checked; raw mapping and indexed-callback hypotheses remain labelled for review.
These findings never adopt code or change match status.

### Keeping a hosted room across app restarts

Rooms left open are reopened automatically when the host starts decompdecomp again. Their invite
code, approval settings and saved members remain; explicitly stopped rooms stay closed. The room
dialog shows restoration failures and lets you retry without discarding membership.

Cloudflare's account-free quick tunnel assigns a new hostname when its process starts. The app
cannot recover that expired hostname: quick-tunnel clients still need the new link. For automatic
reconnection using the same link, choose **Decompile together → Use stable address…** and supply a
reachable LAN/Tailscale/server address and its room port. Share that fixed link once. The address
must remain assigned/reachable and any external proxy/named tunnel must be running. This serves only
the room API, not the app GUI or agent MCP endpoint. A fixed endpoint does not make an offline host
available; clients reconnect when the host and endpoint return.
The sharing dialog remembers the address, port and connection type on this computer for new rooms,
even when closed before sharing. Existing rooms keep their own saved settings.

Agent creation in **Add agents** is a background job. Its progress bar, elapsed time and log distinguish
request acceptance, project/model checks, project preparation, identity reservation and worker launch.
Closing/reopening the popover retains the in-progress view during the same page session; duplicate
submissions are held. Creation completion means the worker records were launched, not that a provider
request is already running: each agent card then shows preparation, sign-in/usage limits and work waits.
If a multi-worker start fails partway through, check the Agents list before retrying; already-created
workers are preserved rather than stopped or duplicated automatically.



### Opening a project while verification is running

Project overview/map reads show the latest saved database snapshot without starting a full saved-file
verification pass. Existing verification continues in the background; changes and judging still use
the synchronization gate. The loading panel reports four actual loading steps (app settings, project
summary, function list, view rendering), elapsed time, and available saved-file verification counts.
Step completion is not an estimate of time remaining; a large function/attempt history may still
make the function-list step longer than the others.

A function’s Deep permuter panel reconnects to its background search when reopened,
including after a page reload. It restores the live scorecard and technical output,
keeps Run disabled while searching, and lets you stop the existing job. The latest
job’s results remain viewable until the app restarts or you start another run.

When closing the window with workers active, the confirmation lists their names,
projects and current states, plus running background tasks. **Stop them and quit**
is highlighted and focused by default; **Keep them working** leaves the app running
without its window.

Single-choice dropdowns share the app’s common control styling automatically,
including function activity sorting and dynamically added settings. They retain
native keyboard navigation and use the current light or dark theme.

The Deep permutation pass groups its limits in a compact form and shows live result
cards with a completed-function count and percentage. Search behavior and resume
details are available under **How this search works**. A queue summary explains whether the pass is
running, waiting, stopped or ready for another turn. Early rotations remain available for a fresh
bounded turn through **Continue remaining functions**; unchanged errors are counted separately
under **need attention**, and functions removed from the build are excluded.

Deep-pass search errors are grouped by reason and next step, with compact links to
the affected functions. Large error lists scroll within the panel.

## Agent efficiency and recovery

Explicit `compile_variants` requests execute the supplied alternatives; automatic
repairs never replace them. Use `try_local_repairs` explicitly, or enable the
existing optional after-submission repair setting. Candidate reads and startup
briefs show a rechecked baseline alongside the historical saved best. Evidence is
cached for its candidate/build/claim scope; unavailable builds are marked unverified.
No historical score is promoted to a current verdict.

A model/configuration rejection pauses the worker immediately. Siblings using the
same connector/account/model reuse that rejection before starting another provider
session. Already running sessions are not interrupted. **Choose another model**
opens the agent launcher; a different model is checked independently. After account
access changes, restart the app to clear the in-memory rejection. Temporary network
and usage-limit errors keep their existing retry/cooldown behavior.

The function inspector identifies C-editor reservations. **Let agents work on this**
releases your own saved function claim; it cannot take another worker’s claim.
Compile or copy unsaved edits first. Compiling from the editor claims the function
again. Agents encountering a user reservation are told to choose other work.

**Agents & models → Agent tools**, and each agent’s **Tools** menu, offer All tools,
Matching essentials, Compiler and data investigation, and Polishing and names
presets. Applying a preset replaces optional tool toggles in that scope; core tools
stay enabled and global restrictions apply to every agent. Individual toggles remain
available. Start a new session or reconnect MCP to receive the changed tool list.
Tool cards report matching/polishing/external call counts and definition character
sizes; these are not token or billing estimates. Task counts backfill surviving
session transcripts once, then accumulate new calls (including failed calls).
Connection probes are excluded. Detailed experiment guidance is available on demand
with `guide(topic="agent-experiments")`; user prompt overrides are preserved.

Local repairs also try bounded index and lifetime alternatives: in-place or separately staged
unsigned index scaling, clearing dead pointer locals, reusing compatible dead locals for indexed
stores, constant-count `for` loops, and splitting mask assignments without changing their types or
mask values. These candidates are shared with the deep permuter and agent local experiments;
only the normal compiler and exact-match judge can save a result.

Team-room member cards show both matched and improved function counts. Improvements count distinct
functions across each person's agents, include matches, and retain credit when another person later
improves the same function. The host supplies these counts to everyone in the room.

Room matches require the host's exact linked-byte and data check. Compiler profiles and private declarations
travel with submitted C; a small source containing neighbouring definitions is recompiled as the selected
function before acceptance. Overlay functions keep separate compiler profiles even when they share a RAM
address. Pending-match retries show the current failure once and recheck cached failures after a host update.
Use updated host and guest clients to preserve overlay identities when syncing profiles, headers and matches.

Applying a compiler suggestion opens a progress dialog immediately. It reports affected-match
checks, saved-score updates and progress-file saving while the work runs in the background. Errors
remain in the dialog, and repeated clicks reuse the running job. Closing the dialog does not cancel
the checks. Existing matches must still pass the normal byte checks before settings change.

Captured repair patterns also guide shared scaled offsets across nearby stores, immediate
pointer-field null tests, byte-array address association inside candidate-owned inline helpers
(including helpers reached through wrapper macros), and trailing stores placed inside both branch
arms. Searches keep these alternatives bounded, preserve protected macro regions and volatile
accesses, and require the normal compiler and byte/object judge before keeping any improvement.

With any project open, choose **Decompile together → Join room…** to join someone else's existing
room. Paste their invite link or public room address and enter your room name. Joining from a project
uses its stored game copy automatically; New project → Join a team’s room still offers a file picker. Existing projects remain in the sidebar. The join option is also available while hosting,
waiting for a room, or restoring a hosted room.

While joining, the progress display identifies connection, game checks, local setup, host verification
and synchronization stages. During saved-work transfer it shows the current function, processed
count, stage percentage, confirmed matches and submitted better attempts. The host rechecks each
result; 100% of that transfer stage does not mean later synchronization has finished.

In **Room match verification**, **Retry all** checks the current pending submissions one at a time,
continuing past individual errors. Individual Retry buttons and Retry all immediately show a spinner,
with duplicate clicks disabled while the job runs. The shared progress display shows processed counts.

Project maps clear the previous project's canvas as soon as you switch games and draw their first
frame only after the loading cover is removed. Late map responses cannot replace a newer refresh.
Compiler-colour analysis is requested when you select the Compiler map, keeping it off the default
Progress view's loading path.

### Commit verified progress from Settings

For ROM projects with a decompdecomp export, open **Settings → Sync to Git repository**,
choose the checkout root, and click **Verify, commit and push**. The app checks new matches
in a private build, saves all portable progress under `progress/` (including drafts, names,
notes, headers and compiler settings), commits those files and pushes the current branch
to its configured upstream using your existing Git credentials. If the branch has no upstream
and the checkout has exactly one remote, it pushes the same branch name there and records
the upstream after a successful push. A new remote branch can be created this way. With
several remotes, configure the intended upstream first. The result shows the commit
and, for GitHub remotes, a link. **Verify and sync** still leaves changes for manual review.

Start with a clean checkout and a configured Git author. An existing upstream is respected;
a checkout without one needs exactly one remote. If the remote
has commits missing locally, pull them first. A failed push keeps the local commit; fix the
credentials or upstream and retry. Nothing is force-pushed. Imported projects already work
in their own source checkout; this export-sync button is currently for ROM projects.


### UI statistics and slow-request diagnostics

Contributor counts and map recency share a project-local history cache. New attempts update it
incrementally; database revisions invalidate it when attempts are corrected or removed, or functions
move between projects. Other projects' work
does not invalidate it. The history index covers measurement columns without loading historical C source.
Live project updates reuse cached room member counts; opening the room panel refreshes those separately.

The app log reports database reads taking at least 250 ms and project UI GET requests taking at least
500 ms (including worker queue time and response encoding). Messages are rate limited per query/route
to once a minute. Database messages identify the caller and a query fingerprint; neither SQL bindings,
SQL text nor URL query parameters are logged.


Live agent cards calculate metrics only for the selected project. Their reset-aware history query
reads measurement columns, never saved C source, and overlapping polls share one refresh. New
attempts invalidate the cache immediately; a five-second refresh also picks up non-attempt changes
such as polishing or restored match attribution. The global agents overview still covers all projects.


Opening a function loads its core detail without waiting for cross-project/SDK name searches.
Expand **Other projects and local SDKs** to load those suggestions through its separate progress
indicator. This applies to both ROM and imported projects; agent detail requests can still include hints.


### Cached project read paths and startup maintenance

Deep-pass queue totals and blocked-candidate explanations reuse validated scope results until their
project's sources, declarations, symbols, compiler inputs or pass records change. Imported projects
also expire these results after ten seconds to detect external file changes. Repair implementation
hashes are reused while file identity, size, modification and change timestamps stay unchanged.
Actual search eligibility and byte verification still use the normal scope and judge checks.

The ROM map caches cross-project byte relationships and invalidates them on target/layout changes;
ordinary progress or source edits do not reread every open project's bytes. Compiler panels use
indexed, cached metadata and schedule automatic surveys/range audits separately from display reads.
Explicit compiler checks retain their existing verification path. Compiler-profile hypotheses share
compact evidence extraction across flag requests and refresh when examples change; every proposed
profile still requires normal rejudging before use.

Optional console initialization and SDK naming begin after a seven-second startup grace period,
with two seconds between tasks and the last-opened project first. Foreground navigation can postpone
a task briefly (up to three additional seconds). Autosave, claim cleanup, room reconnects and agent
resume retain their existing startup paths. Deferred maintenance stops when the app quits/restarts.

Function inspectors now bound chart payloads and load older notes on demand. Deep-pass
queues batch their reads, and worker progress updates coalesce while improvements and
completion remain immediate. Optional request and browser timings are documented in
[interactive app performance](docs/APP_PERFORMANCE.md#queue-and-inspector-throughput).

Agent sessions now return structured waiting state for empty queues, share scoped saved-C
rechecks, include focused instruction feedback, retain explicit correctness alternatives,
and hand released work to bounded background CPU repairs. See
[agent workflow efficiency](docs/AGENT_WORKFLOW_EFFICIENCY.md) for limits and verification.
Local repair searches can test candidate-owned integer prototypes against referenced matched callees,
with at most three individual corrections and one combined candidate. Shared declarations and existing
matches stay untouched; each candidate still goes through the ordinary compiler and exact judge.

Compiler-comparison preparation reports checked functions, total functions and a percentage while checking
eligibility. Each pass batches previous-result lookups and reads shared headers, symbols and compiler
installations once. Shared JSON encoding is reused without changing the comparison digest format, so
unchanged completed comparisons continue to be skipped. Compilation and byte verification still run normally.
### Incremental live views and restart caches

Agent card counters consume only new attempt measurements after the first load. Historical corrections,
deletions, function moves, and metric resets rebuild their counters; match ownership is checked separately.
Polishing totals are fetched for all displayed workers together. The cache does not retain historical C.

Live polls opt into section signatures: unchanged overview, claims, and session sections are omitted, while
events and attempts retain their normal cursors. Older API clients receive full responses. Hidden map panels
defer refreshes until opened. Cross-project byte-comparison IDs and normalized ROM fingerprints persist as
private derived metadata in SQLite and rebuild when their input revisions change; neither cache establishes
a verified C match. Imported object fingerprints keep their existing file-based expiry.

SDK naming reads narrow metadata and briefly reuses unchanged ROM naming checks (at most 60 seconds; imported
projects and active claims retain retry checks). Autosave reuses the project inventory and scopes contributor
lookups to agents referenced by the saved functions. It checks dirty work every second, with full external-file checks at most 15 seconds apart;
existing dirty-function tracking still controls what is rendered and written.

The function drawer requests a lightweight saved-data preview first, on the small-request queue. It shows saved
C and assembly before generating a machine draft or loading history, calls, compiler comparisons and verification.
A separate status bar follows those details, with retry on failure. Imported projects show saved C first and
then load their current source and object comparison. Late responses are scoped to the selected function and
never overwrite unsaved edits. Existing full function API responses and the normal byte judge are unchanged.

Deep passes shuffle lightweight function identities and dispatch eligible work while preparing the queue.
The queue keeps at most two turns per worker outstanding, loads candidate source data in small batches, and
shows checked/total/percentage progress while preparation is underway. Never-tried candidates skip historical
scope comparisons; completed/error records still receive them. Shared headers, declaration indexes and symbol
lookups are reused during preparation. Each dispatched search checks fresh build inputs, preserving existing
completion fingerprints, cancellation and normal byte verification.

### Incremental history and map refreshes

History readers track the latest correction for each attempt. Attaching score metadata to a new attempt no
longer forces a full recount; corrections behind the reader's cursor, deletions and layout changes still rebuild
results. Contributor summaries copy only changed nested records when extending a snapshot.

Function maps retain immutable previous snapshots and reread changed function rows, falling back to full maps
for structural or shared-evidence changes. Latest-row markers are retained per function rather than recording
an unbounded stream of map events. Boundary analysis ignores score-only changes. SDK identity caches depend on
fingerprints, naming metadata, selected libraries and inventory changes; imported objects keep a short expiry.
Session handoffs use indexed lookups within the selected helper's project sessions.

Slow-read logs now break out connection setup, Python lock waiting, SQL execution and row fetching. With
`DECOMP_PERF=1`, these phases also appear in `Server-Timing`. Execution includes waiting within SQLite or the
operating system; the lock-wait figure measures the app's Python lock only. Logs do not include SQL or bindings.

### Suggestion and worker evidence reads

Cross-project name suggestions shortlist indexed metadata before fetching target bytes. Only the strongest
ranked names/IDs/scores are cached; source details remain a separate final-result lookup. ROM fingerprints
ignore compiler/room settings and symbol previews, rebuild for actual byte/relocation/name/ABI inputs, and share
concurrent builds. Explicit refreshes and imported-object expiry still apply.

Repair examples maintain a compact compiler-profile projection transactionally, including migration of existing
evidence, updates and deletion. Profile reads retain the newest-128-example scope without parsing full source
JSON. Room member requests share a three-second refresh and batch worker, match and claim lookups.

Worker candidate scopes reuse revision-tracked headers, symbol indexes and hashed matched-callee evidence.
Transactions bypass these caches, and external imported-project dependencies keep their fresh file checks.
The bounded shared cache retains recently used entries and never evicts an active build. None of these caches
accepts a match: the normal compiler and byte judge remain authoritative.

### Resuming interrupted agents

After an app restart, interrupted Claude Code and Codex CLI agents resume their saved conversation ID from
their existing worker folder. API agents restore their latest complete conversation exchange, scoped to the
same endpoint and model. Current rules and tools still apply. The worker receives its saved function handoff
and must reacquire eligible work through ordinary claims; another worker's claim is never taken. Other CLI
agents receive the function handoff and existing saved project notes in a new conversation. Completed ordinary
sessions still rotate normally. Missing native CLI transcripts fall back to the saved handoff. This applies to
conversations recorded after updating; previously discarded context cannot be reconstructed.
Suspicious MIPS function extents can be audited in Settings → Code coverage → **Review & repair boundaries**. Preview original assembly, repair delay slots/joins/end addresses, classify padding/data, or explicitly keep intentional entries. Automatic work waits for review; decisions retain source/history and have guarded undo. See [coverage review](docs/CODE_COVERAGE_REVIEW.md).

Legacy MIPS GCC compatibility exposes `__builtin_mips_debug_break()` for the fixed
hardware debugger breakpoint used by assertion/error paths. This is a decompdecomp
compiler compatibility intrinsic, not an ISO C feature or a native GCC 2.7 builtin.
It emits BREAK 9, takes no arguments and permits execution to resume in a debugger.
The implementation is selected by the pinned KMC-compatible compiler pack; submitted
assembly and arbitrary instruction encodings remain forbidden. Other compiler packs
need their own verified intrinsic integration before this call can be used there.
Referenced fixed intrinsic templates travel with rebuildable ROM exports and verified
repository sync headers, so standalone builds that invoke the compiler directly retain
the same operation. Unrelated sources keep their existing prelude.

Boundary review now explains the specific warning beside neighbouring original instructions and before/after
extents. **Ask an agent to investigate** starts one advisory worker on the selected entry for up to ten minutes.
It records supporting evidence, counter-evidence, uncertainty and instruction links, without claiming, compiling
or editing functions. Review its proposal, preview the exact change, then run safety checks and save. Safety
checks protect existing work; they do not prove function ownership. This guided repair flow covers mapped
MIPS ROM projects; other processors and imported builds still need adapters.

### Keep matches when closing projects

Settings → **Local match library** lists saved functions and the games that share them, most widely
shared first. Verified matches are saved automatically in `.decomp/match-library.sqlite3`, separately
from the active project database. Existing matches are indexed in the background; **Index existing
matches** runs that pass manually. Closing/removing a project saves its matches before deleting its
app records, even if you also remove the project folder. Notes and unfinished attempts are not archived.

A shared function has one fingerprint record and links to its games/original function names. Identical
source and header blobs are stored once; distinct verified source/compiler variants remain available.
Matches from closed projects participate in normal free cross-project searches. A destination still
has to compile and pass its own exact byte or object comparison. No game bytes are stored in the library,
and it is not uploaded. Copy this database with your app data when moving to another computer.

The **Matches from other games** panel shows the current discovery stage, donor game and
function being checked. Its time and candidate-check budgets are configurable, with quick,
one-hour and overnight presets. Manual passes allow up to 24 hours and 1,000,000 checks,
including unfinished donor C. Each project remembers the controls on this device. The
budget bar shows checks used, not whole-library completion; a pass stops when either
limit is reached or eligible candidates run out. **Stop** keeps verified results.

### Background responsiveness

Dirty progress continues saving on the one-second background cycle. Unchanged progress
folders receive a deep file check every 15 seconds; explicit saves and checks before
judging still inspect files immediately. Startup scans and full or post-match reuse
passes share one maintenance lane across projects, with cooperative yields during
long naming, indexing and fingerprint loops. Reuse compile parallelism also respects
the configured background worker limit. SDK signature corpora are indexed once per
architecture/revision and filtered for each project's selected libraries. Persistent
local tool connection failures retry progressively less often, up to five minutes
plus a small worker-specific offset; stopping a worker still interrupts the wait.

Startup maintenance appears as individual **Archive verified matches**, **Console
upgrades** and **SDK naming** tasks in Background work. Project tasks show which
project they belong to and their own CPU thread. Stop cancels queued tasks or asks
running tasks to finish their current safe step; completed work is retained. One
heavy task runs at a time. Startup tasks are offered again after restarting the app.
Unchanged ROM match archives keep a persistent input stamp across app updates, avoiding repeated
archiving; explicit indexing and imported projects retain full checks. Archive scans read saved evidence
in bounded batches, look up only referenced symbols, and share header analysis and text hashing within
each batch. Stop retains completed batches; project changes are reread between batches.
Room matches send the compiler and flags recorded with each verified function. When older submissions lack compiler settings, the host tries the installed profiles assigned to the project, including compiler ranges and individual functions, and still requires exact linked bytes.

Room verification recovers generated entry names that changed between overlay imports. Historical bodies queued against another overlay at the same RAM address are checked against the named entry; only an exact rebuild can count, and the original submission identity is retained.

Saved-progress verification shows a project banner with the reason for rechecking, completed function counts, a measured progress bar and an estimated remaining time once enough functions have completed. Verification can be paused and resumed from the banner.

Room verification batches saved-progress synchronization around the queue, so applying several matches does not repeatedly rejudge the project. Each submission is still independently byte checked, and its table row updates as it completes.

Saved-progress rechecks select functions whose effective compiler profile, complete header context or source changed. Other matches remain confirmed. Shared symbol/layout changes and unrecognized header inputs still require a full recheck until their dependencies can be identified safely.

### C header history storage

C header versions retain their original text, unit, author and version IDs, while identical
content is shared by SHA-256 and compressed with zlib. Existing databases migrate atomically
on opening with the updated app; older app versions cannot read this storage format.
The app caches recently decoded content and leaves metadata-only queries compressed.

Settings → **Saved C header history** shows logical header text versus shared compressed
content. Manual cleanup keeps current versions, attempt/repair references, recovery history
and headers read during this app run. It never removes saved C attempts or changes matching.
**Compact database file** reclaims reusable SQLite pages separately. It temporarily holds
database writes, requires free drive space of at least twice the database file size, and
can be stopped; interrupted compaction rolls back. SQLite journal/readers can affect the
amount reclaimed. Neither header cleanup nor compaction runs automatically.

Compiler scratch files use `.decomp/compiler-tmp` on the configured `DECOMP_HOME` drive,
so a full system temporary drive does not prevent an app stored on another drive from
compiling. `DECOMP_COMPILER_TMPDIR` selects a different compiler scratch parent. Without
either environment setting, standalone compilation keeps the OS temporary-directory
default. C and assembly workspaces are unique per invocation and removed on normal exit
or failure. An unavailable selected parent produces an error rather than silently using
another drive. Disk-full, quota and open-file-limit failures are reported as host resource
problems and do not consume a candidate repair attempt. This does not relocate SQLite
sort files or imported projects' own build-system scratch files.

Room submissions leave the active verification queue when every profile in their current search has failed. Their source and diagnostics remain archived; a new source/header submission creates new evidence. Partial searches and temporary claim/input failures remain pending.

### Applying a verified match while an agent is working

**Test all non-matches → Recheck & apply** can save an exact match even when an agent
has claimed the function. The final check still verifies the source against the current
target, header and compiler inputs. Only a saved exact match releases the old claim.
The displaced CLI conversation restarts with another eligible function; API helpers discard
obsolete tool calls at the next response or tool boundary. Workers that already moved to
another function keep running. Failed checks keep their claims and saved work.

### Codex tool startup

App-managed Codex sessions require decompdecomp's MCP server to connect before the model
starts. Codex waits up to 30 seconds for startup, including focused/resumed sessions.
If the required connection fails, the helper displays the underlying startup diagnostic
and retries with connection backoff before starting a model turn. Session tokens are
redacted from diagnostics. The separate authenticated app preflight and actual CLI
`project_info` handshake remain in place.
