---
name: match-function
description: Match one function by hand against the running decompdecomp app, then turn what the manual work taught into a Deep Permuter or app improvement so the next such function is matched automatically. Use for "get func_X in PROJECT to match", for a stuck near-match, and as the working method under /goal when the goal is a match.
---

# Match a function by hand, then teach the permuter

The aim is two things, and a run that delivers only the first has not finished:

1. **The function matches** (the app's judge says byte-identical), or you can say precisely what blocks it.
2. **The app gets better at it.** Every manual step that worked (or that the permuter should have found and
   didn't) becomes a tested rewrite, search guide or bug fix, so the permuter finds it itself next time.

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
- Declaration order, `register` and integer width hints often produce identical RTL: test one, don't grid them.
- **IDO bit-packed unpackers** (an N64 audio-header function, `ido-7.1`, exact; the evidence is in
  COMPILER_MATCHING_LEARNINGS.md). The shape that matched: a `u8 *` parameter; stack arrays declared highest frame
  offset first (IDO lays the first-declared highest); one `for (i = 0; i < N; i++)` writing `a[i]`, `e[i*13+k]` from
  `p[i*7+5+k]`. Explicit induction pointers (`*pa++`) and a walked `q += 7` did not match: IDO places the
  induction-variable updates itself, and only the all-index form put them where the target has them. IDO evaluates
  the operand written first first (`(p[0] & 0xF) << 2 | (p[1] >> 6) & 3` is not `(p[1] >> 6) & 3 | ...`). IDO refuses
  `void *` arithmetic; GCC accepts it. Bit-field structs never produced `lbu`/`sra` at all.

## 4. Search only when you can name the mismatch

A blind hill-climb through the app's `/api/try` is too slow. With `fast_judge` it is cheap: random statement
swaps, call-wrapper toggles and local type changes took one function from 1090 to 785 in a round of six seeds, then
plateaued: two more rounds, including token-level rewrites (`x += 1` vs `++x`, `& 0xFFFF` vs a cast), found
nothing. Use it to clear the easy rows, then stop and read the diff: a search that cannot name what it is looking for
will not find the last rows. Verify its best in the app before believing it.

The Deep Permuter: `judge.py permute SLUG FUNC MINUTES VARIANTS` (a function with no scored C is refused:
`judge.py seed SLUG FUNC` judges the app's untouched draft first). It restores the best, tries random rewrites
and local repairs, and saves every verified improvement. Check the result with `judge.py job ID` and the function's
Deep permuter tab. If it errors, that is a bug to fix (see PR "TracedVariant" for an example), not a reason to
wait.

## 5. Teach the permuter (required before you stop)

For each manual step that moved the score, answer: **could the permuter have done this?**

- Yes, but it didn't: find out why (cap, ordering, budget, a rewrite that never applies to this shape) and fix it.
- No: write the rewrite or the search guide. Smallest useful form: a function in decomp-repair that takes source
  and returns variants, with a test that fails without it (see `call_scope_repairs.py` for the shape). Prove it by
  running it on the function you just matched: starting from the state *before* your fix, it must reach the fix.
- **Prove it in a real run, not by calling the rewrite.** Calling `draft_repairs.variants` on the original draft
  is not proof: the search saves its own best in a printed dialect (`*((u8 *) (((char *) p) + 5))`, `v = v + 2;`, a
  braced `goto`) and adds devices such as the argument home-slot pointers, and a rewrite written against m2c's raw
  spelling never fires on that. First test your rewrite on the function's *current best source* from `judge.py best`
  as well as on the draft. Then run `scripts/scratch_run.py`: a throwaway app, a fresh import of the user's own
  ROM, the app's untouched draft as baseline, the stock Deep Permuter with one compiler profile, and it exits 0 only
  when the search ends "Matched". Build its environment from the worktree with your change
  (`UV_PROJECT_ENVIRONMENT=/tmp/dd-scratch-venv uv sync`), never the one the user's app runs from. If the run
  does not match, read why (which family fired, what the profile probe said) and fix that before stopping.
- Record the evidence in `docs/COMPILER_MATCHING_LEARNINGS.md`: exact compiler and flags, the source change, the
  measured before/after, and failed experiments. Say which are controlled results and which are one function.
- A change here needs its PR (AGENTS.md): branch `<type>/<name>`, test, `uv run ruff check`, merged after creation.
  Say in the PR which features you ran and how, and for a library change the tag you bumped. Other sessions release
  the library too: before tagging, `git fetch --tags` and read `git ls-remote origin refs/tags/vX.Y.Z`; a tag that
  exists is taken (use the next version, never move a tag), and after the library's release check its archive
  contains your module before bumping the app's pin.

If you could not match it, still do step 5 for what you learned, and leave the function's note with the blocker.

## 6. Finishing, and using this under /goal

Under `/goal`, the condition is only met when **both** hold, and you should say so in the goal text:

- the function's judged score is 0 (`judge.py diff` reports the app's verdict; re-read it, don't recall it), **or**
  you have stopped with a specific, evidenced blocker and the user agreed to stop; and
- the permuter or app change from step 5 is merged (or an open PR with its test passing), `scratch_run.py` has
  reported PROVEN for it (say what it printed), and your final message gives the before/after
  evidence. "The rewrite produces an exact variant when I call it" does not meet this.

Example goal: `/goal Run /match-function on func_X in PROJECT until it is judged 0, then land the permuter
change that would have found it, with a test.` A long search with no new idea is not progress: when the
score stops moving, go back to step 1 and read the diff again before spending more compiles.

Final message: the score now, what matched it (or blocks it), the PR links, and what the permuter still can't do.
