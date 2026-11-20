# Paired Luna and Sol matching replay

Use a private, offline copy of the app home with its SQLite backups and project
tree. Update the project roots in the copied catalog and project database to
point inside the copy. Keep the copied transcript database alongside its
project database. Provide any tools the copy already uses, including its
pinned compiler. The runner opens no app window, starts no project helpers
and never adopts a result. Imported projects run diagnostic objdiff checks
and restore the tested source after each build.

## Freeze and verify the starting cases

```sh
python -m decompnet.app.model_replay --copy COPY --project PROJECT \
  --cases PRIVATE_CASES --freeze-history --limit 6
```

The historical selector looks for a recorded Luna claim followed by an exact
Sol attempt on the same function and recorded context version. It uses the C
from the actual Luna claim response and independently checks that it compiles
to a miss and that the saved Sol submission is exact. Any recorded compiler,
header and target scope must match. Older records without that scope still
require both current judge checks. The resulting comparison holds the replay
build fixed across arms. It cannot reconstruct compiler settings absent from
older historical records. The optional `--allow-submission-start` fallback
labels an earlier measured Luna submission distinctly when no claim packet
survives.

For imported fixtures or cases without transcripts, use `--freeze-manual INPUT`.
The input JSON contains a `cases` array with distinct `name` and `id` values, a
complete unresolved `source`, an exact `control`, and `context_version` for ROM
cases. Optionally add `stages`, a chronological list of earlier Sol candidates.
The manifest contains private code. Keep it outside the repository.

## Compare the replay arms

With a separately configured Codex profile identified by `CODEX_HOME`, run:

```sh
python -m decompnet.app.model_replay --copy COPY --project PROJECT \
  --cases PRIVATE_CASES --output PRIVATE_RESULTS --trials 6 --seconds 240
```

Each arm starts with the same candidate, target assembly, declarations,
starting diff and effective compiler settings. Use `--arms` to choose order and
`--case` to run particular cases. Every unique submission, including a
compile error, spends one candidate check. ROM reports also count actual
compiler invocations, which can differ on language fallback. Imported
builds may use several tools per check, so their compiler count is unknown.
The clock and candidate limit are the same for each independent arm. Baseline
and historical exact-control verification run before the arms.

- **sol** and **luna** receive the same evidence and judge feedback.
- **luna-historical** gets one prose hypothesis derived from the earliest
  reverified Sol edit that recovers target instructions and improves the
  score. The diagnostic model can see the private before/after edit. The
  Luna request receives its hypothesis and prediction, with the edit itself
  withheld. A missing usable hint is a skipped arm.
- **luna-diagnostic** gets one bounded Sol diagnosis after two unsuccessful
  candidate checks without a new best. Sol sees the current evidence and has
  zero compiler trials and no historical solution. Its call shares the arm's
  elapsed-time allowance. Luna submits and the judge verifies all candidates.
- **luna-assisted** can read the local target register trace, function contract
  evidence and a discriminating probe. It spends the same budget on up to two
  selected local repairs and one combination of separately measured,
  nonoverlapping regional improvements. After a plateau, it can restart once
  with the candidate, current diff and measured effects of the prior attempts.

The replay sends selected candidate C, relevant declarations, target assembly
and judge feedback to the configured models. The historical diagnosis also
uses the captured before/after edit. Results contain hashes, scores, timings
and counts. Treat the copied project, manifests and temporary provider
conversation history as private. Keep cases for choosing the policy separate
from the held-out cases used to evaluate it. Judge-exact recovery is the
primary outcome. Report compile checks, provider failures and Sol calls
alongside it.

## Reduce successful edits and reuse them on other cases

Use `--learn-recipes PRIVATE_RECIPES --case TRAINING_ID --ablation-trials 12`
to try removing or isolating edits under the copied judge. A supported
expression recipe can transfer only to a disjoint case with the same
architecture, compiler identity, flags and observed mismatch class. Use
`--use-recipes PRIVATE_RECIPES` in a separate replay. Every transferred edit
still spends a candidate check and needs an ordinary exact verdict.

The current-input agent search brief can also show the target trace,
discriminating probe and explicit contract evidence. Agents can constrain
`try_local_repairs(anchor=...)` to an exact C span and opt into a combination
check with `compile_variants(combine=true)`. The latter reserves one of the
six normal trials for the combination. Both operations keep their existing
claim and judge checks.

## Guided work and separate skill measurements

Include `luna-guided` to compare a compact workflow against the existing arms.
It measures up to three applicable local options, reserves a compiler check
for the model, and asks the model which continuation to pursue. A locally
exact option is credited to local search in the report. Later model turns
have one objective, the current C, one measured mismatch window, and up to
three recent outcomes. A failed build or regression restores the best
measured parent for the next prompt.

Use `--score-skills` with all arms in a separate comparison. It spends one
additional model call per arm to ask for a diagnosis category and predicted
target instruction. The report distinguishes agreement with the judge's
mismatch category from a predicted site actually disappearing. It also
counts candidate submissions that the judge could score, first improvements,
and recovery to the previous best after a failed or worse submission.
A prediction with no comparable measurement remains untested. These
measurements describe performance on the frozen task and do not establish
causality for the proposed source explanation.
