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
  Your claim can clash with another ("you hold another claim"): that is transient, `judge.py` retries.
- Game data (ROMs, extracted code) is read only and never copied into the repo or a PR.
- Use the scratchpad for experiments, a worktree + branch + PR for repo changes (AGENTS.md workflow).

## 1. Orient (5 minutes, do not skip)

```bash
python3 .claude/skills/match-function/scripts/judge.py best SLUG FUNC /scratch/best.c
python3 .claude/skills/match-function/scripts/judge.py diff SLUG FUNC
```

- Read `best.c` and the **diff of the best source**, not the score. Group mismatches: register choice, instruction
  order, missing/extra instructions, branch shape, stack frame. Say which group dominates.
- Read the function's notes (`GET /api/function` → `notes`) and the history. Earlier agents' dead ends are
  evidence; repeat none of them.
- **Saved sources go stale when symbols are renamed.** A source using a name that no longer exists doesn't link
  ("not a known symbol") and scores terribly. Rewrite to the address names the assembly shows (`D_80102EFC`).

## 2. Work fast and locally

The app takes seconds per compile. The period compiler on your machine takes 0.3 s. Use it for thinking and for
searching, and the app only to confirm:

```bash
.venv/bin/python .claude/skills/match-function/scripts/local_score.py cand.c --rom ROM --offset OFF --size N
.venv/bin/python .claude/skills/match-function/scripts/local_score.py cand.c ... --dumps   # RTL passes
```

`--dumps` writes the compiler's loop pass (`.loop`), schedulers (`.sched`, `.sched2`) and register allocation
(`.greg`) beside the source. When a register or an instruction sits in the wrong place, read these to see which
pass put it there: it is usually faster than guessing at source shapes. Local score is a guide; a candidate that
looks better locally still has to beat the best in the app.

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

## 4. Search only when you can name the mismatch

A blind hill-climb through the app judge is too slow and found nothing the permuter doesn't. A local-score
climb (thousands of compiles) is cheap, but only worth starting from a source whose remaining diff is mostly
registers and order. Verify its best in the app before believing it.

The Deep Permuter: `judge.py permute SLUG FUNC MINUTES VARIANTS`. It restores the best, tries random rewrites
and local repairs, and saves every verified improvement. Check the result with `judge.py job ID` and the function's
Deep permuter tab. If it errors, that is a bug to fix (see PR "TracedVariant" for an example), not a reason to
wait.

## 5. Teach the permuter (required before you stop)

For each manual step that moved the score, answer: **could the permuter have done this?**

- Yes, but it didn't: find out why (cap, ordering, budget, a rewrite that never applies to this shape) and fix it.
- No: write the rewrite or the search guide. Smallest useful form: a function in decomp-repair that takes source
  and returns variants, with a test that fails without it (see `call_scope_repairs.py` for the shape). Prove it by
  running it on the function you just matched: starting from the state *before* your fix, it must reach the fix.
- Record the evidence in `docs/COMPILER_MATCHING_LEARNINGS.md`: exact compiler and flags, the source change, the
  measured before/after, and failed experiments. Say which are controlled results and which are one function.
- A change here needs its PR (AGENTS.md): branch `<type>/<name>`, test, `uv run ruff check`, merged after creation.
  Say in the PR which features you ran and how, and for a library change the tag you bumped.

If you could not match it, still do step 5 for what you learned, and leave the function's note with the blocker.

## 6. Finishing, and using this under /goal

Under `/goal`, the condition is only met when **both** hold, and you should say so in the goal text:

- the function's judged score is 0 (`judge.py diff` reports the app's verdict; re-read it, don't recall it), **or**
  you have stopped with a specific, evidenced blocker and the user agreed to stop; and
- the permuter or app change from step 5 is merged (or an open PR with its test passing) and is described in
  your final message with the before/after evidence.

Example goal: `/goal Run /match-function on func_X in PROJECT until it is judged 0, then land the permuter
change that would have found it, with a test.` A long search with no new idea is not progress: when the
score stops moving, go back to step 1 and read the diff again before spending more compiles.

Final message: the score now, what matched it (or blocks it), the PR links, and what the permuter still can't do.
