---
name: match-function
description: Match one function by hand against the running decompdecomp app, then turn what the manual work taught into a Deep Permuter or app improvement so the next such function is matched automatically. Use for "get func_X in PROJECT to match" or a stuck near-match. It runs as a goal by itself: it does not stop or ask until the function is judged 0 and the permuter improvements are merged.
---

# Match a function by hand, then teach the permuter

**This skill runs as a goal, always.** Invoking it sets the goal for the whole session, as if the user had typed
`/goal`: *the function is judged 0 and every permuter improvement found on the way is merged.* You work until that
is true, turn after turn, without waiting for the user. The rules of that mode:

- **Never stop, ask or hand back while the score is above 0.** No "keep going or stop?" questions, no wrap-up with
  buttons, no final report of a function that is not matched. A turn that ends with the function unmatched ends
  with the next action already running (a permuter job, a search, a PR in flight), never with a question.
- **There is no "blocker" exit.** A function that will not match is a gap in the permuter or the app: find it
  (step 5), land it, and continue. If one idea runs dry, take the next: read the diff again, change the angle
  (other compiler profile, the neighbours' matched sources, the permuter's rejected variants, a scheduler-level
  reading of the target), run a longer permuter. Only the user can end the goal, by telling you to stop.
- **Keep a visible plan and say what you are doing in a line** when you start a long run, so a user following along
  can see the loop is alive. Long runs go in the background.
- **Never idle while the Deep Permuter runs.** Start it (background, thousands of variants), then in the same turn go
  back to manual matching: read the diff, write candidates, compile them locally (`compile_snippet.py`, grids over
  source shapes) and judge the promising ones in the app. Waiting on a job with nothing else in progress is wasted
  time. Check the job between your own attempts (`judge.py job ID`), and fold what it finds into your next idea. A
  manual candidate that beats the saved best is saved by the judge; the running search picks up the better best on
  its next restore, so do not stop one for the other.
- When context grows long, keep going from the plan and the function's notes; do not wrap up early.
- Only when the judge says 0, and the permuter PRs are merged with the proof from step 5, write the final message.

The aim is two things, and a run that delivers only the first has not finished:

1. **The function matches** (the app's judge says byte-identical), or you can say precisely what blocks it.
2. **The app gets better at it.** Every manual step that worked (or that the permuter should have found and
   didn't) becomes a tested rewrite, search guide or bug fix, so the permuter finds it itself next time.

## The order of work: permuter first, then keep hunting until it matches

The permuter improvement comes **before** the match, not after it, so every other user gets it while you work:

1. Orient (step 1), name the mismatch, and try a few quick hand ideas (steps 2-4). Do not sink an hour in here.
2. **As soon as you know what the permuter lacks** (a rewrite it has no form of, a family that never fires on this
   shape, a cap or budget that cut the search, a bug in a run), land that improvement *first*: test, PR, merge
   (step 5), a library release when it is a decomp-repair change. This happens even though the function is not
   matched yet. A permuter run that failed to reach the near-match is itself the lesson: find out why and fix it.
3. Then go back to the function and **keep hunting until the judge says 0**: re-read the diff, run the improved
   permuter on it (with a real variant limit: a few thousand, not 4), try the next idea.
4. Every time a hunt turns up another thing the permuter could do, repeat 2 before continuing 3. Each loop merges a
   PR, so the permuter improves for everyone as you go.

Do not stop to ask whether to keep hunting, and do not stop at all until the function is judged 0 (see the goal rules
above). "Score unchanged after N ideas" is not a reason to stop or ask. If you have no idea the permuter lacks yet,
that is step 5's question to answer honestly (what would have found the near-miss's last row?), not a licence to
skip it: a near-match that the permuter cannot close is always a permuter gap.

The permuter's rewrites live in the decomp-repair library (AGENTS.md, "Agents": change it there, tag, bump the tag
in `pyproject.toml`, `uv lock`). What runs candidates stays in `src/decompnet/app/`. Read that section first.

## Rules that keep other people's work safe

- **Never restart, update or quit the running app, and never `uv sync` under it.** Other agents use it. If it
  quits or says "shutting down", wait for it; do not start another. Work on a copy for anything destructive.
- Find the app: `ps -E -o pid,command | grep -E 'bin/decomp( |$)'`, then `<home>/.decomp/server.json` (port,
  secret, pid). Dev checkouts use `~/Decomp-RetroReversing-dev` on 6474. More than one app, or none: ask.
- Judging goes through the app (`scripts/judge.py try`), so the user sees your attempts; it is credited to "you".
  Your claim can clash with another ("you hold another claim": an app job or an earlier attempt of yours under the
  same identity): `judge.py` releases that claim and asks again. `judge.py` is standard library only; the other
  scripts need an environment with decompnet (`.venv/bin/python`, read only: do not `uv sync` it).
- Game data (ROMs, extracted code) is read only and never copied into the repo or a PR.
- Use the scratchpad for experiments, a worktree + branch + PR for repo changes (AGENTS.md workflow).

## 0. A run in progress is never a reason to wait

Deep Permuter jobs and any other long search run in the background (`run_in_background`, or
`judge.py permute` which returns a job id). Starting one is the *beginning* of a stretch of work, not the end of a
turn. While it runs, in this order:

1. **Do the other half of the goal.** If the run is the proof for a library change, the next thing is the part that
   does not depend on it: the evidence entry for COMPILER_MATCHING_LEARNINGS.md, the PR text, the next rewrite's test.
2. **Investigate the mismatch yourself, in a different direction from the run.** The search explores random rewrites;
   you read. Dump the compiler's passes (step 3b), decode the target's block layout instruction by instruction, or
   try hand shapes. Never repeat what the search is already doing (its log names the families it tries).
3. **Mine the run as it goes.** `judge.py job ID` and the function's saved best show each verified improvement as it
   lands: read the new best's source and diff now, since a step the search found (even at a score still far from 0)
   is a lead for your own investigation and for the rewrite you will write. Keep a run's home (`--keep`) so its source
   can be read.
4. **Check on it by looking, not by blocking.** No `Monitor`/`sleep` loop as the only activity. A wait is only
   acceptable when every item above is done and you say so in one line.
5. **Say what you are doing.** A one-line status naming the run and the work done beside it; silence while a run
   goes reads as being stuck.

When the run finishes, compare its best with yours before trusting either.

## 1. Orient (5 minutes, do not skip)

```bash
python3 .claude/skills/match-function/scripts/judge.py best SLUG FUNC /scratch/best.c
python3 .claude/skills/match-function/scripts/judge.py diff SLUG FUNC
```

- **Is it the project's compiler?** Before any source shape, read the target's idioms and ask which compiler writes
  them (this is the cheapest question and the one that cost most when skipped: 243 attempts under the wrong
  compiler). Signals that name another compiler: a byte or halfword load shifted with `sra` (IDO writes it; every
  GCC here folds it to `srl`, whatever the casts, types or flags), register moves written `or rd,rs,$zero` (SGI) versus
  `addu` (GCC). Test it, don't assume it: write the idiom as a few lines of
  C and run `scripts/compile_snippet.py TOOLCHAIN snippet.c` for each installed toolchain, or the whole candidate with
  `--asm` to see how many instructions agree. The deep pass probes other profiles (`move_fingerprints`), but only a
  candidate the other compiler can build and whose shape fits it will ever match there.
- Read `best.c` and the **diff of the best source**, not the score. Group mismatches: register choice, instruction
  order, missing/extra instructions, branch shape, stack frame. Say which group dominates.
- Read the function's notes (`GET /api/function` → `notes`) and the history. Earlier agents' dead ends are
  evidence; repeat none of them.
- **Saved sources go stale when symbols are renamed.** A source using a name that no longer exists doesn't link
  ("not a known symbol") and scores terribly. Rewrite to the address names the assembly shows (`D_80102EFC`).

## 2. Work fast and locally

The app takes seconds per compile. A pinned compiler through the app's own compiler path takes about a second. Use
`scripts/compile_snippet.py` for thinking and searching, and the app only to confirm:

```bash
.venv/bin/python .claude/skills/match-function/scripts/compile_snippet.py ido-7.1 cand.c --asm FUNC.s
.venv/bin/python .claude/skills/match-function/scripts/compile_snippet.py gcc-2.7.2-kmc idiom.c -- -G0 -mips3 -mgp32 -mfp32 -O2
```

It prints how many of the target's instructions agree and where the first differences are (registers and
immediates normalised; the callee's address ignored). When a candidate reaches 1.000 it still has to pass the app's
judge: `judge.py try`. A candidate that looks better locally has to beat the best in the app.

**For a search, use the app's own judge standalone.** `decomp judge-worker` returns exactly the app's score (a
source judged 1090 in the app judged 1090 here) in about 0.1 s, needs no claim, runs beside the app without touching
its state, and parallelises across processes. `scripts/fast_judge.py` wraps it (`Judge(...).score(source)`).
**Do not search on an approximation**: a home-made scorer (opcode alignment plus register counts) ranked candidates
wrongly: a 1000 → 680 local improvement was 1090 → 1030 in the app. Confirm any result once with `judge.py try`.

## 3. What has worked (extend this list when you learn more; it is the permuter's to-do list)

- **Base-relative stores.** `*(s8 *)((char *)&sym + off) = v` stays `off($sN)` only when the first use of the
  symbol's address is wrapped in `do { call(&sym + k); } while (0);`; without the wrapper the compiler folds them
  to absolute `lui/sb` pairs. (3185 → 1760 on one function.) The library has the wrapper (`call_one_shot_scope`)
  but not the search that pairs it with base-form stores.
- **Natural loop forms beat m2c's gotos only when the target's registers agree.** A clean `for` loop hoisted array
  bases into registers the target recomputes with `lui $at`. Keep the form that gives the target's addressing.
- **Recompute, don't cache.** If the target recomputes `n & 1` at the use site, a `temp = n & 1` local is wrong.
- **Entry copies that aren't folded** (`andi $s0,$s5,0xffff` before the loop) mean the loop variable is assigned at
  the top of the body from the counter, not initialised from a constant.
- **Loop constants** (`li $s7,2` hoisted) land where the scheduler's priorities put them, not where you assign
  them; reordering or moving the assignments changed nothing. Unsolved: what makes the target's scheduler put them
  early. If you solve it, write it down here.
- **A `do { } while (0)` is a scheduling boundary for GCC's second scheduler** (a display-list tail, `gcc-2.7.2-kmc`,
  60 -> 0). When the first scheduler's output (`cc1 -dS`; `-dR` is the second; run the compiler's own `gcc -E` first)
  already has the target's order and the final output hoists an add above a store, wrap the second half of the run,
  through the final call, in the loop, and copy the call's global arguments into locals *before* it. `goto`, empty
  `while (0)` and `if (0)` vanish before scheduling and do nothing; inline `asm` is refused by the judge. The wrapper
  can swap `$s0`/`$s1`: keep the call inside it. decomp-repair `scheduling_scope` proposes it.
- **m2c's shared colour/flag block (`v = K; if (c) { ..; v = D; goto L; } else { L: use(v) }`)**, KMC gcc 2.7.2: the
  value belongs where the *join* is, not before the `if`. The early assignment gives `v` its own register and
  removes the `li` from the branch delay slot; sunk to the label, written as its literal (not a search-made constant
  local) with `u8` stores (an `s8` store of 0xFF is `li -1`), it was exact. The polarity of the `if` (which arm holds the
  `goto`) decides which arm is out of line: read it from the target's delay slots. Library: `join_initializer_sink`,
  `branch_polarity`.
- **Several changes that are each no better alone** are the normal shape of a late match. Offer the combination as
  one child, and keep non-improving children for a second step (`operand_order_pass`'s combined step).
- **A copy that lands in the wrong register (`$v1` for `$v0`) beside a `goto blockN` join.** m2c writes `v = s;`
  before the inner guard *and* at the label; the early one occupies the register. Merge the guards and put `v = s;`
  at the end of each arm (both stay: one definition is folded away). `join_copy_sink` and `shared_result_copy` in
  decomp-repair do it. A library family only helps when `app/draft_repairs.py` calls it and the installed library is
  current: if the app didn't offer one, check both before writing a new rewrite.
- Declaration order, `register` and integer width hints often produce identical RTL: test one, don't grid them.
- **A commutative op whose constant must come first** (`and $v0,$v0,$v1` with the mask in `$v0`, the target's registers
  all right and only the operand order swapped). cse always puts a known constant second, so no spelling of
  `x & mask` gets it; hide the constant from cse with `(x & mask) | mask`, which combine folds back. Found by dumping
  cc1's passes (3b); the family is `mask_operand_order` (decomp-repair 0.5.201). Same shape may exist for `or`/`xor`/`add`.
- **IDO bit-packed unpackers** (an N64 audio-header function, `ido-7.1`, exact; the evidence is in
  COMPILER_MATCHING_LEARNINGS.md). The shape that matched: a `u8 *` parameter; stack arrays declared highest frame
  offset first (IDO lays the first-declared highest); one `for (i = 0; i < N; i++)` writing `a[i]`, `e[i*13+k]` from
  `p[i*7+5+k]`. Explicit induction pointers (`*pa++`) and a walked `q += 7` did not match: IDO places the
  induction-variable updates itself, and only the all-index form put them where the target has them. IDO evaluates
  the operand written first first (`(p[0] & 0xF) << 2 | (p[1] >> 6) & 3` is not `(p[1] >> 6) & 3 | ...`). IDO refuses
  `void *` arithmetic; GCC accepts it. Bit-field structs never produced `lbu`/`sra` at all.

## 3b. Out of hand-written shapes? Trace the compiler instead of guessing

Three or four structurally different sources landing on the same score (or the same missing/extra instruction) means
the shape is decided by a compiler pass, not by how you spell the C. Stop writing variants and find the pass:

1. **Dump the compiler's own intermediate code.** The pinned GCC `cc1` is a native binary you can run directly
   (`<toolchains>/gcc-2.7.2-kmc/cc1`; the folder is `~/Library/Application Support/decompnet/toolchains` on macOS,
   `~/.local/share/decompnet/toolchains` on Linux). Run it in a scratch folder on a candidate with the project's flags
   plus dump letters, and read the files it writes beside the source:

   ```bash
   cc1 -quiet -O2 -G0 -mips3 -mgp32 -mfp32 -dr -dj -dl -dg -dL cand.c -o cand.s    # cand.c needs the typedef prelude
   ```
   `cand.c.rtl` (after expansion), `.jump` (after jump optimisation: where a branch was inverted, threaded or a block
   merged), `.cse`, `.loop`, `.greg` (after register allocation), `.sched`/`.dbr` (scheduling, delay slots) are
   the stages. Diff the dump of your best candidate against the dump of a variant that moved the difference: the
   first stage where they differ is the pass that decides it. For the target, you can't dump it, but its
   instructions tell you which stage's output they are (a `li; j` pair in a branch's fall-through is jump-optimiser
   output, an extra move is register allocation, a nop is delay-slot filling).
2. **Read that pass's source.** The compilers here are GNU GCC builds: 2.7.2 is public (`jump.c`, `cse.c`, `loop.c`,
   `local-alloc.c`/`global.c`, `sched.c`, `reorg.c`, `stmt.c`/`expr.c` for how C turns into RTL). Reading the pass
   tells you which *input* it keys on (a label's use count, whether a block is reached by a fall-through, whether
   a constant is a CONST_INT in a register or an operand, a variable's `REG_USERVAR_P`), and that is the C you
   must write. The source is a reference, not game data, but it is a download: ask the user before fetching it, and keep
   it outside the repo. A copy already on the machine (a decomp project's `tools/`, `ultralib-source` under the
   toolchains folder) needs no download.
3. **Turn the finding into a rewrite.** The pass's rule is the permuter's guide: a family in decomp-repair that
   produces the source shape the rule needs (step 5). Record the rule, not only the spelling, in
   COMPILER_MATCHING_LEARNINGS.md ("jump.c inverts `if (x) goto A; goto B;` unless an insn sits between them" is
   reusable; "this function needed `goto`" is not).

## 4. Search only when you can name the mismatch

A blind hill-climb through the app's `/api/try` is too slow. With `fast_judge` it is cheap: random statement
swaps, call-wrapper toggles and local type changes took one function from 1090 to 785 in a round of six seeds, then
plateaued: two more rounds, including token-level rewrites (`x += 1` vs `++x`, `& 0xFFFF` vs a cast), found
nothing. Use it to clear the easy rows, then stop and read the diff: a search that cannot name what it is looking for
will not find the last rows. Verify its best in the app before believing it.

The Deep Permuter: `judge.py permute SLUG FUNC MINUTES VARIANTS` (VARIANTS is a limit: use thousands, e.g. 5000; a run with 4 tests nothing) (a function with no scored C is refused:
`judge.py seed SLUG FUNC` judges the app's untouched draft first). It restores the best, tries random rewrites
and local repairs, and saves every verified improvement. Check the result with `judge.py job ID` and the function's
Deep permuter tab. Start it, then keep matching by hand while it runs (goal rules above). If it errors, that is a bug to fix (see PR "TracedVariant" for an example), not a reason to
wait.

## 5. Teach the permuter (do it first, merge it, then continue hunting)

For each manual step that moved the score, answer: **could the permuter have done this?**

- Yes, but it didn't: find out why (cap, ordering, budget, a rewrite that never applies to this shape) and fix it.
- No: write the rewrite or the search guide. Smallest useful form: a function in decomp-repair that takes source
  and returns variants, with a test that fails without it (see `call_scope_repairs.py` for the shape). Prove it by
  running it on the function you just matched: starting from the state *before* your fix, it must reach the fix.
- **Never run `scripts/scratch_run.py`, or any throwaway app, fresh import or long proof search.** It takes too much
  CPU for the whole machine (other agents and the user's app share it), and the user tests every repair themselves in
  their own app later. Your job is **new repairs in the permuter**, not proving old ones: spend the time finding the
  next gap and writing its rewrite. To check a repair, test it on the function's *current best source* from
  `judge.py best` as well as on the draft (the search saves its own best in a printed dialect,
  `*((u8 *) (((char *) p) + 5))`, `v = v + 2;`, a braced `goto`, and a rewrite written against m2c's raw spelling never
  fires on that), add the unit test, wire it into the permuter, **merge, and then connect to the running app to make
  sure the function is matched there too** (`judge.py diff SLUG FUNC` says "matched"; the saved best is the source that
  judged 0). Say in the PR what you ran and that the real proof is the user's run.
- Record the evidence in `docs/COMPILER_MATCHING_LEARNINGS.md`: exact compiler and flags, the source change, the
  measured before/after, and failed experiments. Say which are controlled results and which are one function.
- A change here needs its PR (AGENTS.md): branch `<type>/<name>`, test, `uv run ruff check`, merged after creation.
  Say in the PR which features you ran and how, and for a library change the tag you bumped. Other sessions release
  the library too: before tagging, `git fetch --tags` and read `git ls-remote origin refs/tags/vX.Y.Z`; a tag that
  exists is taken (use the next version, never move a tag), and after the library's release check its archive
  contains your module before bumping the app's pin.

Step 5 is merged *before* you continue hunting, not saved for the end. If you still cannot match the function
afterwards, leave its note with the evidenced blocker.

## 5b. Mistakes this method has already made (each cost a run or an hour)

- **Decode the target's own assembly before writing shapes.** The project's `asm/nonmatchings/**/FUNC.s` has the
  offsets and delay slots; `judge.py diff` rows do not. Reading delay slots (`bnez` with `li` in its slot, a `j` whose
  slot is the other path's value) gave the branch *polarity* in minutes after an hour of spelling variants that all
  had it backwards. Do this in step 1 whenever the diff is a layout difference, not only when stuck.
- **A search's own scaffolding is part of the diff.** `__repair_constant`-style locals and `s8 *` stores of constants
  above 0x7F (`li -1`, not `li 0xff`) look like m2c output and are not the game's: write literals, `u8` stores.
- **Never `git stash`, `git checkout` or `uv run` in a worktree whose venv a run uses.** `uv run` re-syncs the
  environment to whatever `pyproject.toml` says at that moment: a stash that reverted the pin silently downgraded
  the library, and a run failed at its first step with "No module named". Run `python -c "import decomprepair.<your module>"` in the run's own interpreter before starting.
  Resolve merge conflicts in a separate `--detach` worktree, not the one a run is using; never remove that worktree.
- **A run's first minute is where it fails.** Read its log once, at 30 seconds, then go back to step 0's work.
  "error checking baseline" means the search never started: read the job error, don't wait for it.
- **Other sessions share the machine.** The scratch port, the tags in the library (`git ls-remote --tags` right
  before tagging; main's version may already be ahead) and the library's `main` all move under you. Use a free
  `--port`; kill a run by its pid, never `pkill -f` on a script name (it kills other sessions' runs and your own
  `Monitor` loops, whose command lines contain the name).
- **The app you judge through can disappear** (the user quits it). Don't start one. Score locally with
  `compile_snippet.py` (it needs the file's own typedefs: `f32` is not in its prelude) against the project's
  `asm/nonmatchings` file; a candidate that scores the same as a known-exact source is exact. Judge in the app when
  it is back.
- **One manual step is often several permuter gaps.** Here the match needed three changes at once (sink, literal, `u8`);
  each alone left the score unchanged or worse, so a one-step-at-a-time search cannot see them. The rewrite should
  emit the whole combination as one child, and its test should assert the combination, not the parts.
- **Check what already exists before writing a rewrite.** `passthrough_argument` already did the argument fix and
  `symbol_width_repairs` was one regex away from the cast fix; ten minutes of grep over the library's docstrings
  found both. Run the existing families on the saved best first: what they don't produce is the gap.

## 6. Finishing (the goal is met)

The goal from the top of this file is met only when **both** hold:

- the function's judged score is 0 (`judge.py diff` reports the app's verdict; re-read it, don't recall it); and
- the permuter or app change from step 5 is merged (or an open PR with its test passing), the function is
  confirmed matched in the app after the merge (`judge.py diff`), and your final message gives the before/after
  evidence. Do not run `scratch_run.py`; the user tests the repair in their app.

A long search with no new idea is not progress, and not a stopping point: when the score stops moving, go back to
step 1 and read the diff again before spending more compiles.

Final message (only once the goal is met): the score (0), what matched it, the PR links, and what the permuter
still can't do.
