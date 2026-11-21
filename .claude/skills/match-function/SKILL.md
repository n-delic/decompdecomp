---
name: match-function
description: Match one function by hand against the running decompdecomp app, then turn what the manual work taught into a Deep Permuter or app improvement so the next such function is matched automatically. Use for "get func_X in PROJECT to match", a stuck near-match, or just a project ("match a function in PROJECT"), when it claims a random open function nobody else holds.
---

# Match a function by hand, then teach the permuter

In Claude Code, use `/match-function PROJECT [FUNCTION]`. In Codex, use
`$match-function PROJECT [FUNCTION]`. Both clients load this file and use the same scripts.
For persistent Codex goal tracking, start with `/goal` and include the skill, the project and the
optional function in the goal. The completion condition is a recorded score of 0 and any permuter
improvements found during the run merged with their tests.

- Keep working toward that condition while there is a useful next step. When an approach stalls,
  read the diff again, check other compiler profiles and nearby matched sources, or test repair
  families on the current best. If further work genuinely needs user input or external access,
  report the exact blocker and the current verdict.
- **Keep a visible plan and say what you are doing in a line** when you start a long run, so a user following along
  can see the loop is alive. Long runs go in the background.
- **Never start the Deep Permuter** (`judge.py permute`, `scratch_run.py`, any long search job). The user runs it in
  their live app and it already uses every core. To test a repair, call it directly: the library's family (or the
  app's `draft_repairs.variants`) on the current best source, scoring its variants with `fast_judge.py` and
  confirming the best one with `judge.py try`.
- When context grows long, keep going from the plan and the function's notes; do not wrap up early.
- Report success after the judge records 0 and any permuter PRs from step 5 are merged.

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
3. Then go back to the function and **keep hunting until the judge says 0**: re-read the diff, call the improved
   repair families directly on the new best (never a Deep Permuter job), try the next idea.
4. Every time a hunt turns up another thing the permuter could do, repeat 2 before continuing 3. Each loop merges a
   PR, so the permuter improves for everyone as you go.

Keep hunting while there is a useful next step (see the completion condition above). "Score unchanged after N ideas" is not a reason to stop or ask. If you have no idea the permuter lacks yet,
that is step 5's question to answer honestly (what would have found the near-miss's last row?), not a licence to
skip it: a near-match that the permuter cannot close is always a permuter gap.

The permuter's rewrites live in the decomp-repair library (AGENTS.md, "Agents": change it there, tag, bump the tag
in `pyproject.toml`, `uv lock`). What runs candidates stays in `src/decompnet/app/`. Read that section first.

## Which function: claim it first, always

Before any other step, generate a holder name for this session with
`python3 -c "import secrets;print('match-'+secrets.token_hex(4))"`. Pass it as `--holder NAME` on
**every** `judge.py` call in the session. The app keeps one claim per holder and uses the window's
shared "you" when the holder is omitted. A unique holder keeps concurrent sessions from taking
over or releasing one another's claims.

- **Only a project given** (no function): `judge.py --holder NAME claim SLUG` claims a random open function that no
  agent, app job or other session holds, and prints its name. That function is the goal's function from here on.
  Never pick one yourself from the function list: a function you chose without claiming may be the one another
  session is already working on. If it says nothing is left, tell the user; that is the one case with nothing to do.
- **A function given**: `judge.py --holder NAME claim SLUG FUNC` claims it by name. If it is held by someone else,
  say so and ask the user whether to take another (`claim SLUG`), rather than working on it beside them.
- Claims expire when idle: run `claim SLUG FUNC` again (it renews your own claim) before a long stretch of local
  work, and after any message that the claim was lost.
- When the goal is met (or the user stops it), `judge.py --holder NAME release SLUG FUNC` gives it back. A matched
  function needs no claim any more, but release it anyway.

## Rules that keep other people's work safe

- **Never restart, update or quit the running app, and never `uv sync` under it.** Other agents use it. If it
  quits or says "shutting down", wait for it; do not start another. Work on a copy for anything destructive.
- Find the app: `ps -E -o pid,command | grep -E 'bin/decomp( |$)'`, then `<home>/.decomp/server.json` (port,
  secret, pid). Dev checkouts use `~/Decomp-RetroReversing-dev` on 6474. More than one app, or none: ask.
- Judging goes through the app (`scripts/judge.py --holder NAME try`), so the user sees your attempts, credited to
  your holder. Your claim can clash with another of the same holder ("you hold another claim": an earlier attempt
  of yours): `judge.py` releases that claim and asks again. That is why each session needs its own holder. `judge.py` is standard library only; the other
  scripts need an environment with decompnet (`.venv/bin/python`, read only: do not `uv sync` it).
- Game data (ROMs, extracted code) is read only and never copied into the repo or a PR.
- Use the scratchpad for experiments, a worktree + branch + PR for repo changes (AGENTS.md workflow).
- **Never `git pull`/`checkout` in the primary checkout**: the user's live app runs from its `.venv`, and a pull
  leaves the source ahead of it (the app's judge-worker then failed to import decomprepair). Read the latest skill
  with `git fetch -q origin && git show origin/main:.claude/skills/match-function/SKILL.md`; run `fast_judge.py` or a
  judge-worker from a private venv built in your own worktree (`UV_PROJECT_ENVIRONMENT=<scratch venv> uv sync`).
- On macOS, BSD `sed -i` needs `sed -i ''`. For long commands, use the client's background
  execution tools. In Claude Code, use `Monitor` when available. In Codex, poll the running command
  or session with its process tools.

## 1. Orient (5 minutes, do not skip)

```bash
python3 .claude/skills/match-function/scripts/judge.py --holder NAME claim SLUG          # no function given: prints FUNC
python3 .claude/skills/match-function/scripts/judge.py --holder NAME best SLUG FUNC /scratch/best.c
python3 .claude/skills/match-function/scripts/judge.py --holder NAME diff SLUG FUNC
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
- **Edit the current best; don't rewrite the function from scratch.** Start from `best.c` (or the app's draft) and
  reach the match by a list of small, named edits: sink this assignment, swap this operand order, make this store
  `u8`, wrap this call. Keep that list (with each step's score) in your notes as you go. Each edit is a candidate
  permuter rewrite, so the list is step 5's to-do list; a clean rewrite that matches teaches the permuter nothing,
  since nobody can tell which of its hundred differences mattered. If a large restructure seems needed, do it as
  the smallest edit that shows it, and when a from-scratch source does match, diff it against the best and reduce
  it to the minimal set of edits before writing any rewrite.

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
- **Literal constants in a loop, and the one-shot wrapper on the right call.** Constant locals (`c2 = 2;`) stay where
  they are set; literal stores in a loop are hoisted by loop.c and float up to the nearest do-while(0) fence. A
  function at 120 matched once the loop's constants were literals and its wrapper moved one call earlier (120 → 60
  → 0). The library has both (`single_use_inline`, `call_one_shot_scope_shift`, decomp-repair 0.5.269).
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
- **Statement order is worth a grid; declarations aren't.** When the diff is a few loads/stores in the wrong order
  (global address loads, a flag store, a byte read-modify-write), permute those 3-4 statements with `fast_judge.py`
  (a few dozen compiles, seconds): one switch case went 610 -> 420 -> 240 -> 0 that way, where reordering by eye stalled.
  A byte global updated as `b = g; b &= ~1; g = b;` (m2c's split form) versus `g &= ~1;` changes the order too;
  decomp-repair `compound_global_update_repairs` offers both (0.5.237).
- **A commutative op whose constant must come first** (`and $v0,$v0,$v1` with the mask in `$v0`, the target's registers
  all right and only the operand order swapped). cse always puts a known constant second, so no spelling of
  `x & mask` gets it; hide the constant from cse with `(x & mask) | mask`, which combine folds back. Found by dumping
  cc1's passes (3b); the family is `mask_operand_order` (decomp-repair 0.5.201). Same shape may exist for `or`/`xor`/`add`.
- **Register swaps between saved registers (`$s1`/`$s2` exchanged, everything else right): read the priorities,
  don't guess.** Compile the candidate with `cc1 ... -dl -dg` and read the `.lreg`/`.greg` table (uses, live length,
  priority, register; `app/allocation_priority.py` parses it). GCC 2.x gives saved registers in order of
  `floor_log2(uses) * uses / live_length`, so the fix is a use more or less, or a shorter range, for the pseudo that
  must rank higher. A KMC 2.7.2 function with three array loops (180 -> 0): index each loop by the counter
  (`base + i * 0x18`, not m2c's separate byte-offset variable), guard each loop on the counter (`if (i < n)`, not
  `if (n > 0)`: one more use), give a value m2c had stored in a reused counter name its own temp, and use one counter
  for all three loops; that lifted the counter's priority 0.82 -> 0.94 past the offsets (0.93). Then trim m2c's frame
  union to the target's frame (`measured_trim_frame_view`). decomp-repair `guarded_offset_index` (0.5.238).
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

4. **Register-only rows: dump priorities, not passes.** `-dl -dg` prints each pseudo's priority; compare it with the
   target's register order and change uses/live ranges (above, in 3). An empty `.lreg` means cc1 didn't get the
   flag or failed to parse: run the compiler's own `gcc -E` first and strip comments.

## 4. Search only when you can name the mismatch

A blind hill-climb through the app's `/api/try` is too slow. With `fast_judge` it is cheap: random statement
swaps, call-wrapper toggles and local type changes took one function from 1090 to 785 in a round of six seeds, then
plateaued: two more rounds, including token-level rewrites (`x += 1` vs `++x`, `& 0xFFFF` vs a cast), found
nothing. Use it to clear the easy rows, then stop and read the diff: a search that cannot name what it is looking for
will not find the last rows. Verify its best in the app before believing it.

Do not start the Deep Permuter (`judge.py permute`): the user runs it in their app and it takes every core. Its
saved results (`judge.py job ID`, the function's Deep permuter tab) may be read as leads.

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

  Tag gotchas seen in one batch: `git fetch --tags` "would clobber existing tag" means a tag you made locally was
  released meanwhile by another session: `git tag -d` it, take the next free version, tag only right before the
  push; and a run's "No module named decomprepair.<x>" meant the pinned tag predates your module (check the archive).
- After the first 0, re-read `judge.py diff`: it must say **"matched best 0"**. "open best N" means the match was not
  recorded (one run's 0 wasn't until resubmitted); resubmit the exact source with `judge.py try`.

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
- **Other sessions share the machine.** The scratch port, the tags in the library (`git ls-remote --tags` right
  before tagging; main's version may already be ahead) and the library's `main` all move under you. Use a free
  `--port`; kill a run by its pid, never `pkill -f` on a script name (it kills other sessions' runs and any monitoring commands whose command lines contain the name).
- **The app you judge through can disappear** (the user quits it). Don't start one. Score locally with
  `compile_snippet.py` (it needs the file's own typedefs: `f32` is not in its prelude) against the project's
  `asm/nonmatchings` file; a candidate that scores the same as a known-exact source is exact. Judge in the app when
  it is back.
- **One manual step is often several permuter gaps.** Here the match needed three changes at once (sink, literal, `u8`);
  each alone left the score unchanged or worse, so a one-step-at-a-time search cannot see them. The rewrite should
  emit the whole combination as one child, and its test should assert the combination, not the parts.
- **`judge.py best` writes the function without the project's typedefs** (`u8`, `f32`, its structs). Fed to
  `fast_judge.py` or `compile_snippet.py` as is, it fails to parse and the score comes back `None` (looks like "no
  verdict", not an error). Build one header once (typedefs plus the externs) and prepend it to every candidate; and
  test scores with `is None`, never `score or BIG`, which turns an exact 0 into a failure.
- **`fast_judge.Judge` needs the target bytes**: `judge.App().function(SLUG, FUNC)` gives `rom_offset`, `size`,
  `vram`; slice `<project>/rom.z64` (the project folder in the app's home, `ps -E` shows which home) with them.
- **Check what already exists before writing a rewrite.** `passthrough_argument` already did the argument fix and
  `symbol_width_repairs` was one regex away from the cast fix; ten minutes of grep over the library's docstrings
  found both. Run the existing families on the saved best first: what they don't produce is the gap.

- **Edit the best, keep an edit list, and score each row** (edit, score alone, score combined). It worked: the
  180 -> 0 table above came straight from it, and it showed which edits only help together (guard on the counter
  alone went 60 -> 193). Sharpen it: when an edit makes it worse, keep it in the list and test it again after each
  later edit, rather than dropping it; and say for each row which compiler fact it changes (addressing, a priority,
  the frame), so the rewrite you write in step 5 emits the whole set as one child.
- **zsh doesn't word-split `$VAR`**: a `for f in $FILES` or a flag string in a variable passes one argument. Wrap the
  judge in a shell function (`fj(){ ... "$@"; }`) or a small Python driver that keeps one `fast_judge.Judge` open.
- **m2c reuses one variable name for unrelated values** (a counter later holding a call's result). That ties their
  registers together; split the name before reading any register diff.

## 6. Finishing (the goal is met)

Confirm these results before reporting success.

- the function's judged score is 0 (`judge.py diff` reports the app's verdict; re-read it, don't recall it), "matched best 0";
- the `docs/COMPILER_MATCHING_LEARNINGS.md` entry is written (step 5; skipped until reminded in one run); and
- the permuter or app change from step 5 is merged if an improvement was found, the function is
  confirmed matched in the app after the merge (`judge.py diff`), and your final message gives the before/after
  evidence. Do not run `scratch_run.py`; the user tests the repair in their app.

A long search with no new idea is not progress, and not a stopping point: when the score stops moving, go back to
step 1 and read the diff again before spending more compiles.

Release the claim (`judge.py --holder NAME release SLUG FUNC`), then report the score, what matched the function, the relevant PR links, and any remaining permuter gaps.
