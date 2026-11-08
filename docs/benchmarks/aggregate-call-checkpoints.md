# Frozen checkpoint recovery

Equal budget: 32 candidate checks / 30 seconds per checkpoint. No AI tokens.

| Checkpoint | Start → best score | Exact recovered | Compiler calls | Wall seconds | Proposal seconds | Compiler seconds |
| --- | --- | --- | --- | --- | --- | --- |
| 97.2% | 325 → 325 | no | 33 | 14.33 | 1.55 | 9.88 |
| 98.0% | 230 → 230 | no | 33 | 13.42 | 1.52 | 9.91 |
| 98.4% | 185 → 185 | no | 33 | 13.44 | 1.51 | 9.65 |
| 98.9% | 125 → 0 | yes | 2 | 0.67 | 0.00 | 0.58 |
| 99.4% | 60 → 0 | yes | 2 | 0.66 | 0.00 | 0.59 |

Independent frozen checkpoint recovery; no live adoption. Repeated checkpoints are not distinct functions.

Wall time differs from summed stage/worker work. Baseline setup is separate; compiler invocations include baseline and failed compiles. Unknown instrumentation is null.

## What this measures

Five chronological sources of the same private 117-instruction N64 function were
frozen before replay. The target image, source/header hashes and installed compiler
were checked independently for each checkpoint. No live project was changed and no
known matched source was offered as a candidate. This is rediscovery of an existing
match, not five new functions or a cross-game success rate.

Profile: pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
The ordinary linked-byte/data judge supplied exactness. No complete-image rebuild
is claimed. Compiler calls include one baseline compile per checkpoint. Timings
are a single local observation, not a hardware-independent performance promise.

Both successful checkpoints used `revisit_one_shot_scope`. In particular, the
98.9% helper-based checkpoint already needed only wrapper removal; the additional
manual argument bundle was not independently necessary for exactness. Earlier
manual chronology had obscured this simpler path.

The first three checkpoints consumed their 32-trial budget without improvement.
No aggregate dispatch/preparation family was generated for their macro/raw-pointer
forms under the conservative guards. Other families produced proposals but did
not bridge that gap within the budget. This does not prove the functions or
families exhausted. The next research target is evidence-backed macro and alias
coverage, rather than simply raising the random-permutation budget.

## Reproduce on your own frozen exported project

Use `python -m decompnet.app.checkpoint_benchmark --project COPY --function NAME
--checkpoints MANIFEST --output PRIVATE_JSON --markdown REPORT_MD --trials 32
--seconds 30` (one shell command). It only reads the exported project and uses
already-installed registry compilers; exported build commands are not executed.
The manifest contains a `header` entry with `path` and `sha256`, and a
`checkpoints` list with `id`, `path`, `sha256` and `expected_score` for each
unresolved source. Paths are relative to the manifest unless absolute.

Every checkpoint gets a fresh evaluator, output cache and proposal cursor. Changed
source/header hashes or a changed starting score stop replay. The source-free
report includes attempted/generated families, winning path, exact recovery, actual
compiler invocations, wall time, proposal generation, compiler execution and linker
time. Unknown instrumentation is reported as unknown, not as zero cost.

Keep manifests, game source and image files private. Shared reports must describe
the fixed budgets and distinguish repeated checkpoints from distinct functions.

## Guard explanations and optional follow-up analysis

Add `--macro-analysis --minimize-recipe 12` to the command above. Both are
opt-in, private analysis; neither edits a live project or adopts code.

The report now separates call-family eligibility from search coverage. A guard
reason means the generator refused a shape, not that its trial budget ran out.
Generated/tested counts remain separate. Agents receive compact guard explanations
when asking for an ineligible call family, and for scheduling-only near misses.

Macro analysis runs the already-installed pinned GCC with the same flags/header
in its ordinary compiler sandbox, replacing only compilation with preprocessing.
Source/header and expanded-output hashes identify the evidence. The original
source is preserved. Expanded C stays in the private analysis buffer; the summary
contains eligibility evidence rather than game code. Custom commands, unsupported
drivers, missing compilers and uncaptured includes give an explicit skip. No tools
are downloaded. Expanded pointer expressions do not establish non-aliasing and
are not automatically rewritten back into the source.

A repeated five-checkpoint run recovered the same two late checkpoints, in one
repair trial each. Macro preprocessing succeeded for all five. The original and
expanded forms still hit the broad qualifier guard: an unrelated `volatile`
declaration in the header was enough to disable dispatch generation. The report
identifies header versus function tokens. This is an eligibility limitation, not
evidence that more random trials would help; the guard has not been relaxed.

Recipe reduction uses a separate budget after recovery: up to the requested edit
trials plus a control check and final recheck. The report counts actual candidate
checks, compiler invocations where known, and elapsed time separately from search.
Every retained reduction must still receive the ordinary exact linked/objdiff
verdict **and** preserve the complete allocated-object fingerprint, including
other emitted code/data. Token edits preserve whitespace and comments. A failed
or interrupted reduction retains the original verified recipe. Edit indices and
source hashes describe the recipe without publishing source; this is the smallest
verified recipe found within the bounded probes, not a global minimum.

The two automatically recovered wrapper-removal recipes had nothing further to
remove (four checks each including control/recheck). A separate replay of the
historical, longer successful recipe removed one of seven token edits within
24 edit trials: 26 candidate checks / 25 compiler invocations including control
and final verification. The reduced output remained exact. This is analysis of
an existing match, not an additional game match or complete-ROM rebuild.

The shared reduction callback accepts any copied exact judge, including PROJECT
mode. This CLI's frozen exported-ROM adapter remains MIPS-only; other modes must
supply their normal copied build/objdiff evaluator. Native GCC preprocessing is an
explicit adapter limitation, not evidence from a substituted host compiler.

Recipe verification also fingerprints tentative ELF `COMMON` definitions by name,
size, alignment and binding. They do not yet occupy an allocated section, so
section bytes alone would miss their removal or resizing. A pinned KMC control
with a 28-byte tentative global versus an `extern` declaration now has distinct
fingerprints. Objects without `COMMON` keep their historical fingerprints.
