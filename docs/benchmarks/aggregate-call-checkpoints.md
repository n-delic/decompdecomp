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
