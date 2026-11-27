---
name: match-function
description: Match one function by hand against the running decompdecomp app, then turn what the manual work taught into a decomp-repair family or app improvement so the next such function is matched automatically. Use for "get func_X in PROJECT to match", a stuck near-match, or just a project ("match a function in PROJECT"), when it claims the nearest open function nobody else holds (lowest saved score, at most 800 instructions).
---

# Match a function by hand, then teach the permuter

## Never (read first)

- Never print `connection.json` (it holds a bearer token).
- Never delete `/tmp/dd-skill`; if it is missing, re-extract it with `git archive` (see "Start").
- Never leave copies of this skill's text anywhere, including `~/.claude` tool-result files: delete them after reading.
- Never touch, restart or update the user's running app or the primary checkout.
- No Deep Permuter and no `scratch_run.py` (see below).
- **Haiku: never change the repo and never open a PR.** Write the repair report instead (the symptom, the exact C
  before/after, the profile, which function it fixed) in `report_repair` and in your final report; a Sonnet builder
  implements it. **Every other model (Sonnet, Opus, ...): land the repair as a merged PR** (a decomp-repair family or
  an app change; follow the flow in AGENTS.md: decomp-repair PR, release tag, then the pin PR the library-pin workflow
  opens), so the Deep Permuter finds the match by itself next time. Step 5 below is that flow.
- No `try_compiler` and no compiler ranges. The app's `compiler_experiments` trials plus `suggest` (First moves) are the
  way to change a function's profile. The brief's "Closer attempt under another compiler profile" is where to start
  when it is there; before giving up on C that is closer under another profile (`fast_judge.py` records nothing), run it
  through one `compiler_experiments` trial so the app keeps it for the next worker.
- At most 2 parallel judge runs, and one agent runs ONE judge or compile process at a time (parallel grids overloaded the
  machine twice).
- zsh does not split a flag string held in a variable: pass flags literally.

**Small models (Claude Haiku): do not follow this file.** Read [HAIKU.md](HAIKU.md) instead (`next`, `try`, `done`
with `scripts/haiku.py`), one function per agent run.

## First moves, in this order

Most matches in a 1,000-function session came from this order, not from clever spelling.

1. **Read the function's notes and the app's diff** (`get_function` sections `work` and notes; step 1). Earlier dead ends
   are evidence.
2. **`prove_chain.py --slug S --function F --families all --children 40`**, then **`prove_chain.py ... --fp-flip`**. Cheap, and it
   closes many near-misses by itself.
3. **Float constants.** Float pool words in the target (`lui; lwc1` of data) are float literals in C: read the ROM value
   with `read_data` and write `1.5f`. Then try the app's `compiler_experiments` trial with
   `-G0 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp32` when the target never uses odd float registers
   (two-thirds of the N64 functions in the last project needed this per-function profile).
4. **Other compilers.** The target ends `jr ra; nop` (gcc-2.8.1 always fills that slot), or the stretch was built by SN GCC
   2.7.2: try the `sn-n64-272-0001`, `-0004` and `-0006` trials (`-G8 -mips2 -O2`, or `-G0 -mips2` / `-G0 -mips3`).
   libultra and other SDK code: KMC 2.7.2 `-G0 -mips3 -O2 -mgp32 -mfp32`.
5. **Then hand edits** from the fix catalog below.

Rules for the trials:
- The exact flag set matters: `-G8 -mips2 -O2 -mfp32` scored 110 where `-G0 -mips3 ... -mfp32` was exact.
- The app's trial is the truth. In `fast_judge.py`, `--flags` ADDS to the function's own profile; to judge another
  compiler or level pass `--toolchain ID` and `--replace-flags='-O2 ...'` (also after a match changed the function's
  profile). Its `judged with` line (stderr) says what the score is of.
- **An exact trial is applied with `suggest(function=FUNC, trial_id=ID)` WITHOUT `review_only`.** The user has allowed
  compiler-setting changes without asking. (Haiku agents once left seven exact trials queued.) Then check
  `get_function(section="summary")` shows the profile and `matched` 0, and report the profile change.

## Fix catalog (symptom, then the fix)

One line each. Detail is in `docs/learnings/` (`python tools/learnings.py search "<symptom>"`) and in section 3 below.

- **The draft has an extra or missing call argument.** Drop arguments the target never sets up. Add one the callee's
  matched C reads: a register still holding a value at the call is an argument; a float in an integer register is a
  missing argument; a `bnel` where you wrote `bnez` hints an argument already in `$a0`. (`signatures.py`.)
- **A `void` function whose target ends `move $v0,reg`** returns the object: give it the return type.
- **An unfilled delay slot before the epilogue means `$v0` is live:** declare `s32` and return nothing.
- **Scaffolding in a saved best** (`if (0)` goto ladders, loop-weight wrappers, phantom locals, `do { } while (0)` fences):
  strip it (`plain_best.py`). An unused `buf[N]` sometimes really sizes the frame: test before deleting.
- **Two calls where the target has one:** one call per branch (cross-jumping merges the tail).
- **Jump table one entry larger than the cases:** an empty `case`. A switch on a call's result inside a switch: an inner switch.
- **A few stores or loads in the wrong order:** a store-order grid (`grid.py`). More than 8 statements: a move-one-line hill
  climb, not a full grid.
- **The target reads a just-stored field back:** write the read-back.
- **A temporary the target keeps in one register across blocks:** reuse one local across the blocks. A local assigned twice
  moves address and constant builds.
- **Raw offset accesses (`*(T *)((u8 *)p + 0x10)`):** retype as one struct per pointer family. Struct field versus plain
  global alias is a real source difference.
- **`extern u8 X[]` read as `(*(T *)X)`, or array externs under `-G8`:** match the target's gp-relative or `lui` form by
  the declared type.
- **`p[i]` versus a pointer walk:** try both; loop.c reduces `p[k]` itself, so write the index form first.
- **`abs()` and `sqrtf`:** builtins give the target's inline form; a hand-written `if` does not.
- **A loop bound the target keeps in a register:** put it in a local.
- **`cvt` after an int in a float loop:** `x = (f32)i; x *= K`.
- **Float literals versus keeping `extern f32`:** under `-G8` the target reads small-data floats; write the form that gives
  `lwc1 ...($gp)` or `lui; lwc1`.
- **String literals:** a `lui; addiu` pair with a rodata address is a literal, not a symbol.
- **Callee address `(s8 *)&f + 8`:** the target jumps past a prologue; write that call (it also shows the real entry
  offset).
- **A strided table:** one record array (`Row t[]`), not parallel byte tables.
- **`slti` versus `sltiu`:** signed versus unsigned compare; fix the type.
- **Wrong global types and stale offsets in a saved best** are real source errors: fix them, the score may rise first.
- **A twin matched already:** adapt its C, then re-read every constant and callee in yours.
- **Known library code (zlib, libultra, libmus, libgcc):** write it from its reference source, then adjust.

## Claims, submissions and notes

- **Finish a function before releasing it.** `setup.py` refuses to re-claim a function the same session released.
- **Submit EVERY candidate that beats the app's saved score** with `setup.py try` so the progress is kept. Never submit
  an unfaithful source (for example a sign-flipped function that scores lower by coincidence): it cannot be removed from
  the record.
- **Notes** (`add_note`, field `text`) only on functions you hold, and never test notes.
- **`report_repair` after each match.** A profile-suggested match may be refused: that is fine.
- After `suggest`, check `get_function` shows `matched` 0.

## Triage: boundary and SDK

Do not grind these; say so in the note and release.

- **Boundary problem:** a stray word before the prologue, a `break 7` left by the previous function, padding words, a
  first-word branch outside the extent, or an end that swallows the next function. Note the body, release with a
  "boundary" verdict. The user fixes boundaries in Review function boundaries.
- **Probably SDK or hand-written assembly:** a short leaf that reads a hardware status register with unfilled delay slots.
  Say so in the note.
- **Callbacks stored as `func_X + 8`** reveal a real entry offset.

## Tool gotchas

- `passes.py`, `handoff.py`, `dumps.py` and `explain_regs.py` work for every SN profile (sn-n64-281 and the 2.7.2 ones) as for
  the GCC ones: the app runs cc1n64.exe under wibo with `-da`. One command gets everything the old hand-rolled recipe did:
  `passes.py CAND.c --slug S --function F --save DIR` prints the allocation table (priority, refs, live insns), preferences,
  conflicts, both scheduler orders and the loop optimizer's notes (hoists "moved to", bivs, givs combined or refused), and
  writes `DIR/F.i` (preprocessed C), `F.s` (final assembly), `F.{rtl,jump,cse,loop,cse2,flow,combine,sched,lreg,greg,
  sched2,jump2,dbr}` and `F.report.txt`. `preprocess.py` prints the `wibo cc1n64.exe -quiet <flags> IN.i -o OUT.s` line to rerun
  with other dump flags (`-dl -dg -dS -dR -dL -ds`). The gcc-2.8.1-pm native cc1 is still a stand-in for 2.8.1 only apart
  from byte compares and branch delay-slot fills; it is NOT a stand-in for 2.7.2.
- `grid.py` has no `--flags` option: patch a copy.
- **Probing another profile (`fast_judge.py --flags=-mfp32`, `prove_chain.py --fp-flip`)? Dump that compile too:**
  `passes.py`, `explain_regs.py`, `dumps.py` and `alloc_roles.py` take the same `--flags` (and `$DD_EXTRA_FLAGS`). Without it they
  dump the function's own profile, and under `-mfp64` every float register they name is another compile's.
- A long store list (over 8) needs a move-one-line hill climb, not a full grid.
- `report_repair` categories: `mcp.py <holder> report_repair --help` prints the valid ones.


In Claude Code, use `/match-function PROJECT [FUNCTION]`. In Codex, use
`$match-function PROJECT [FUNCTION]`. Both clients load this file and use the same MCP tools.
For persistent Codex goal tracking, start with `/goal` and include the skill, the project and the
optional function in the goal. The completion condition is a recorded score of 0 and any permuter
improvements found during the run merged with their tests.

- Keep working toward that condition while there is a useful next step. When an approach stalls,
  read the diff again, check other compiler profiles and nearby matched sources, or test repair
  families on the current best. If further work genuinely needs user input or external access,
  report the exact blocker and the current verdict.
- **Keep a visible plan and say what you are doing in a line** when you start a long run, so a user following along
  can see the loop is alive. Long runs go in the background.
- **Never start, wait on or reconfigure the Deep Permuter, and never run `scripts/scratch_run.py`.** The user's
  runs use most cores and they check permuter changes themselves. Use the saved results to identify a gap, then
  call one repair directly and score it with `fast_judge` (step 4). The deliverable is a tested permuter
  improvement that can reproduce the measured gain.
- **Every step must be a faithful program.** Never add stores to wrong addresses or values, dead blocks or phantom
  locals (an unused `long long`) just to improve the score; prefer source a developer could plausibly have written.
  When a match relies on scaffolding anyway (a `+ (t & 3)` zero term), say so in the report and the notes.
- **Do not tidy a matched source.** Once the judge says 0, stop: do not spend compiles or time renaming, retyping,
  commenting, reordering or otherwise cleaning it up. Polishing does that afterwards (`app/readable.py`), and the app
  accepts a later exact source for a matched function anyway, keeping the previous one in history. Your job is only
  that each step stays a faithful program (above) and that the scaffolding a match relies on is reported.
- Keep a private checkpoint with the latest live verdict, the measured search seeds and their scores, pending
  MCP calls, the next experiment, and open PRs. Resume from it across context changes.
  A plateau calls for a different experiment. Keep the claim while working on the match.
- Report success after the judge records 0 and (Sonnet, Opus and other non-Haiku models) the tested permuter improvements from step 5 are merged; a Haiku agent reports the repair instead.

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

The permuter's rewrites live in the decomp-repair library (AGENTS.md, "Agents": change it there; the release and the
pin follow by workflow, step 5). What runs candidates stays in `src/decompnet/app/`. Read that section first.

## Start: `setup.py` makes the session and ends it

Scripts live in `.claude/skills/match-function/scripts/`; read the latest from origin/main without touching the primary
checkout: `git fetch -q origin && git archive origin/main .claude/skills/match-function/scripts | tar -x -C /tmp/dd-skill`
(`mkdir -p` it first), then run `python3 /tmp/dd-skill/.claude/skills/match-function/scripts/setup.py SLUG [FUNC]
--repo <primary checkout>`. It picks a holder, builds `/tmp/dd-<holder>/` with a worktree off origin/main (`repo/`, detached:
`git switch -c <type>/<name>` there) and a private venv (synced to that worktree's lock file; after you `git pull`/rebase there, run `setup.py sync <holder>`; never `uv sync` the primary checkout), creates the project-scoped MCP connection (token in
`connection.json`, mode 0600, never printed), claims once (when none is given: the nearest open function, lowest saved score first, of at most 800 instructions;
`--max-size N` changes the cap, 0 for any size; `--random` takes a random one instead) and renews the claim
every 5 minutes until it matches. It prints the environment; use that worktree's own `scripts/` and venv python from then
on. Add the connection to your client as `connection.json` says. Finish with `setup.py stop <holder>`: it stops the renewal,
revokes the connection (that frees the claim even with no compile yet) and removes the whole folder. Release with notes
through MCP first when you learned something.

**Submitting to the app: `setup.py try <holder> CAND.c`** (add the function name as a third argument for another claimed
function). It calls `compile_and_diff` over the session's own MCP connection, the one that holds the claim, and prints the
app's verdict. Use it for every submit and confirmation; do not write your own gateway client, and do not use `judge.py try`
in a session (its claim belongs to the connection, so it answers HTTP 400). `judge.py try` is for a claim made with
`judge.py claim` only.

**Check the claim: `setup.py check <holder>`** (also `status`) asks the app now whether this session still holds its function
and exits 1 when it does not. Run it **every 20 minutes** and **before any long isolated work** (a `grid.py` or
`prove_chain.py` run, a long `fast_judge` search): a claim lapses when the app is slow, another session then claims and
matches the function, and isolated scoring never notices. The renewal retries a failed renewal within seconds and, when
the app says someone else holds the function (or it is matched), writes a `LOST` file in the session folder; from then on
every `setup.py` command, `fast_judge.py`, `prove_chain.py` and `grid.py` print a boxed **CLAIM LOST** warning, and
`setup.py try` refuses to submit. A function this session matched itself is not a lost claim: it is said once,
quietly. **On a lost claim stop working on that function**: if `check` says it is free,
`setup.py reclaim <holder> FUNC` (reread the saved best first: someone may have changed it); if someone else holds it or
it is matched, `release` your notes through `add_note`, then `setup.py release <holder>` and `setup.py reclaim <holder>`
for another function. Never keep working "in the isolated judge" on a function you no longer hold.

**Any other MCP tool: `mcp.py <holder> <tool> ['{"json":"args"}']`** (`get_function`, `get_candidate`, `get_assembly`,
`last_diff`, `report_repair`, `add_note`, ...) over the same connection, printing the result. `mcp.py <holder> --list` lists
the tools; `mcp.py <holder> <tool> --help` shows a tool's arguments with their allowed values (for `report_repair`, the valid
categories, read from the app's own schema; a wrong category is refused with them). It repeats a "still running" reply for
you and never repeats an "accepted ... do not repeat" one. Do not write your own MCP caller or gateway client.

**The session's environment: `setup.py env <holder>`** prints `export UV_PROJECT_ENVIRONMENT=... DECOMP_HOME=...
DECOMP_PORT=...` (the token is never printed); `eval "$(setup.py env <holder>)"` in a new shell, and `setup.py` itself ends with the same line.

The other scripts, run with that venv's python (`$UV_PROJECT_ENVIRONMENT/bin/python`): `fast_judge.py FILE.c --slug S
--function F [--diff [--rows N|all]]` (score, and the app's differing rows: 60 by default, then a "... N more rows" line; `--rows all` shows every row; a session's worktree off an older origin/main may lack `--rows`: `git pull --rebase` there and `setup.py sync <holder>`), `passes.py FILE.c --slug S --function F` (allocation
priorities, register preferences, scheduler orders), `prove_chain.py --slug S --function F --families a,b|all` (greedy
chain of library families, scored), `families.py [words]` (the library's families, one line each), `similar.py SLUG FUNC` (matched look-alikes and their saved C; step 1), `callers.py SLUG FUNC` (every call site with the instructions around it, and whether the callers read a result: the return type; step 1), `grid.py FILE.c --slug S --function F` (every combination of marked alternatives and statement orders, scored; `--by-rows` ranks by the differing rows; step 2), `shared_types.py list|add SLUG` (the shared header's types; propose or add one, step 2), `regions.py` (a large function region by region, step 2),
`tools/learnings.py` (evidence entries; `search "<symptom>"` ranks them by symptom, step 3).

## Connect through the Rust MCP gateway

Use the running app's decompdecomp MCP tools for claims, saved candidates, judging and release.
`setup.py` (above) creates the dedicated connection and claims over it; what follows is what it does, and the manual way.
If the app started this client as a helper, use its existing connection. Otherwise, use a dedicated
project-scoped connection for this session. In the app, open **Agents & models → Connect another AI
tool with MCP**, select the requested project and use the configuration it provides for the client.
Use the Streamable HTTP URL on the Rust gateway's MCP port, with the connection's bearer credential.
The app keeps that port in `.decomp/mcp-gateway.port` in its data home. Let the client supply the
credential. If the gateway or project connection is missing, report which dependency is missing
and continue read-only investigation or isolated scoring while setup is pending. Do not send claims
through the window API or restart the app.

If the app is running without an accessible window, create the same dedicated connection through its local
configuration endpoint: read `port` and `secret` from that home’s `.decomp/server.json`, then POST JSON
`{"action":"create","slug":"PROJECT_SLUG"}` to `http://127.0.0.1:PORT/api/manual-mcp` with
`Content-Type: application/json`, `X-Decomp: 1`, and `User-Agent: decompdecomp-window/SECRET`.
The response contains the project-scoped bearer token. Keep the response and secret private (a scratch file
with mode 0600 if needed); do not print them. Use the separately advertised Rust gateway port for every MCP
call, including claims and judging. This endpoint only creates the connection; it is not a claim or judge API.
If the app or gateway is unavailable, do not start or restart it; continue read-only or isolated work.

Call `project_info` and verify that the connection is scoped to the requested project. Use the same
connection throughout the run so the app keeps attempts and claims under one identity.
If the endpoint disappears, check the running app's advertised gateway port and verify
`project_info` again before sending mutations. An app restart can retire a temporary endpoint.
In a session, `mcp.py` (above) sends the requests; only a client with no session writes raw ones: use a distinct JSON-RPC ID for each request, including retries.
The gateway deduplicates pending calls by connection, tool and arguments.

## Claim one function

- On a reused connection, start with `get_claims` to recover existing work. If it holds this
  function, resume it. If it holds unrelated work, use a separate project connection. A newly
  created connection that has made no claim can go straight to the first claim.
- `setup.py` makes the first claim (once) and renews it. By hand: with your own holder name; **only a project given**:
  call the near-match claim (`claim_function(strategy="nearest", max_insns=800)`) exactly once and use the name the app
  returns; never loop on it. Near-misses are where a hand session finishes and leaves a family behind; take a random one
  (`strategy="random"`, `setup.py --random`) only when asked to, or when nothing under the cap is left (the claim answers
  that nothing is left: raise `max_insns` once). `judge.py claim SLUG --nearest --max-size 800` is the same for a window
  holder.
- **The claim is renewed at least every 10 minutes** (`setup.py` does it every 5; by hand, `claim_function(function=FUNC)`
  again). A lapsed claim lets other agents take the function.
- **An enormous function** (well over ~1000 instructions, or a best in the thousands with no near-miss rows) may be
  released once and another claimed: `release_function` it (with its claim token, `move_on=true`), then
  `setup.py release <holder>` (the renewal must stop renewing it, or it claims the function back every 5 minutes), then
  `setup.py reclaim <holder>` for the next one (the nearest; or `reclaim <holder> FUNC`, `--random`, `--max-size N`;
  it removes the previous function's `start/` files).
  If you keep it, work it with the large-function method (step 2).
- **A function given**: call `claim_function(function=FUNC, fallback=false)` when the tool exposes
  `fallback`. If someone else holds it, report the conflict and continue in the isolated judge.
  Older app versions may assign a different function when a named claim fails. If that happens,
  release the replacement with its own claim token, `move_on=true`, and a note that the named
  task did not include it. Wait for the requested function to become available before retrying.
- If the gateway replies **still running**, repeat the identical tool call with the same arguments and
  connection to collect its result. Keep other experiments local while a claim or compile is pending.
  If it replies **accepted** and says not to repeat, let the app finish and read the notice on the next reply.
- `setup.py` and `mcp.py` do that waiting for you (up to 30 minutes per call, `DD_CALL_WAIT_SECONDS`; progress goes to
  stderr; a dropped connection or 503 from an overloaded app is repeated too). A slow claim at start never tears the
  session down: if the app is still busy when the wait ends, `setup.py` keeps the worktree, venv and connection and
  tells you to run the same command again (same `--holder`), which collects the claim instead of claiming twice. Do
  not re-run setup without `--holder`, and do not `stop` a session just because a claim was slow. `setup.py try`
  keeps a submission that is still in flight (`try-pending.json`) and, on the next `try` with a different source,
  collects the earlier verdict first: a second source is never sent while one is running.
- If the client loses the response without a gateway receipt, recover with `get_claims` for a claim
  or the recorded verdict and history for a submission before retrying. Keep the canonical name and
  current claim token, and wait for the claim to be confirmed before submitting.
- Keep the claim across submissions. A compile renews it. If it expires, reclaim the same
  function when available. If the app ends the claim for low progress, continue in the isolated judge
  during the retry cooldown. Reclaim for a measured candidate once the app allows it.
- On completion or stop, check `get_claims`. If the claim is still held, use `release_function`
  with its current token. Follow the tool's release guidance if the claim has no compile yet.

## Rules that keep other people's work safe

- **Never restart, update or quit the running app, and never `uv sync` under it.** Other agents use it. If it
  quits or says "shutting down", wait for it; do not start another. Work on a copy for anything destructive.
- Local compiler probes need an isolated Python environment with decompnet. If a probe needs
  game data, identify the app's data home as described in AGENTS.md and read the project files there.
- Game data (ROMs, extracted code) is read only and never copied into the repo or a PR.
- Use the scratchpad for experiments, a worktree + branch + PR for repo changes (AGENTS.md workflow).
- **In the primary checkout, never `git pull`/`checkout`/`stash`, `uv sync`, or create branches**: the user's live
  app runs from its `.venv`, and a pull leaves the source ahead of it (the app's judge-worker then failed to import
  decomprepair). Read the latest skill with `git fetch -q origin && git show origin/main:.claude/skills/match-function/SKILL.md`.
  Work in the session's worktree and private venv (`setup.py` makes them; by hand: `git worktree add` off `origin/main` and
  `UV_PROJECT_ENVIRONMENT=<private dir> uv sync`); run `fast_judge.py` or a judge-worker from it.
- On macOS, BSD `sed -i` needs `sed -i ''`. For long commands, use the client's background
  execution tools. In Claude Code, use `Monitor` when available. In Codex, poll the running command
  or session with its process tools.

## 1. Orient (5 minutes, do not skip)

Read `get_function(function=FUNC, section="work")` for the current C, mismatch and recent
measurements. Use `get_candidate(function=FUNC, base="saved_best")` when you need the saved best C
and write it to a scratch file for local probes. Use `get_assembly` for the target instructions.

- **Is it the project's compiler, and is the function's per-function profile right?** Check the compiler and flags
  and the function's current profile (`get_function` summary). IDO idioms mean `ido-7.1` (some twine functions were
  built with IDO 7.1, not KMC GCC); `lui $at; addu $at,$at,off` table access with stepped saved registers means
  `-fno-strength-reduce`; a function wrongly set to `-fno-strength-reduce` makes strength-reduced counter multiples
  unreachable. Report any per-function profile change you make. Before any source shape, read the target's idioms and ask which compiler writes
  them (this is the cheapest question and the one that cost most when skipped: 243 attempts under the wrong
  compiler). Signals that name another compiler: a byte or halfword load shifted with `sra` (IDO writes it; every
  GCC here folds it to `srl`, whatever the casts, types or flags), register moves written `or rd,rs,$zero` (SGI) versus
  `addu` (GCC). Test it, don't assume it: write the idiom as a few lines of
  C and run `scripts/compile_snippet.py TOOLCHAIN snippet.c` for each installed toolchain, or the whole candidate with
  `--asm` to see how many instructions agree. The deep pass probes other profiles (`move_fingerprints`), but only a
  candidate the other compiler can build and whose shape fits it will ever match there.
- **Scoring version 2** charges 150 for a wrong address, constant or callee, so scores may look higher than the old
  scale. A brief may warn the saved best looks suspicious and offer the decompiler's draft as a second start: try
  both. Earlier agents' frame, `volatile` and scratch-array workarounds in saved bests are often worth removing.
- Read `best.c` and the **diff of the best source**, not the score. Group mismatches: register choice, instruction
  order, missing/extra instructions, branch shape, stack frame. Say which group dominates.
- **Look at matched look-alikes first**: `similar.py SLUG FUNC` (a session's python) lists matched functions with
  similar instruction patterns (the app's `find_similar`) with their similarity and prints the saved C of the best
  three (`--sources N`, `--out DIR` writes files, `--all` adds unmatched ones). Their spelling is a lead to adapt; judge it.
- **When the return type or the arguments are unclear, read the callers**: `callers.py SLUG FUNC` (the app's
  `get_callers`) lists each call site with the instructions around it and a verdict. A caller that reads `$v0`/`$f0`
  after the call proves the function returns a value (a void function leaves it unread); a matched caller's C is
  exact; callers that all drop the result only weakly say `void`. Write that result type, then judge.
- Read the function's notes and history with the corresponding `get_function` sections. Earlier agents' dead ends are
  evidence; repeat none of them.
- Keep a newly improved candidate as a private search seed even if it contains a suspicious read.
  Record the issue and keep the prior candidate as a control. Compare the exact judge diffs and, where
  supported, the compiler's allocation and scheduling dumps to find what the edit changed. An identical-arm
  float guard can affect floating-point live ranges before the compiler merges the arms, so removing
  the guard immediately can also discard useful allocation evidence.
- Work from the improved seed. Try one named repair family at a time against it and measure its
  children. For an uninitialized float guard, test alternatives that retain the useful guard or
  lifetime shape while supplying an initialized value, and measure which other repairs combine with it.
  If generation or routing misses the improvement, add a regression that starts from that seed and
  test the emitted child with the exact judge. A lower score alone does not establish source behavior.
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

Use `scripts/compile_snippet.py` for quick compiler probes. Confirm the best candidate with
`setup.py try HOLDER CAND.c` (that is `compile_and_diff` as your session) and use `last_diff` to page the submitted trial's diff.

```bash
.venv/bin/python .claude/skills/match-function/scripts/compile_snippet.py ido-7.1 cand.c --asm FUNC.s
.venv/bin/python .claude/skills/match-function/scripts/compile_snippet.py gcc-2.7.2-kmc idiom.c -- -G0 -mips3 -mgp32 -mfp32 -O2
```

It prints how many of the target's instructions agree and where the first differences are (registers and
immediates normalised; the callee's address ignored). When a candidate reaches 1.000 it still has to pass the app's
judge: `compile_and_diff`. A candidate that looks better locally has to beat the best in the app.

An exact `compiler_experiments` trial under another profile: keep its trial ID and frozen source/flags, then apply it
with `suggest(function=FUNC, trial_id=ID)` (no `review_only`: the user allows compiler-setting changes without asking, see
"First moves") and verify `get_function(section="summary")`. Use `review_only=true` only when a brief says to queue
trials for review.

**For a search, use the app's own judge standalone.** `decomp judge-worker` returns exactly the app's score (a
source judged 1090 in the app judged 1090 here) in about 0.1 s, needs no claim, runs beside the app without touching
its state. `scripts/fast_judge.py FILE.c --slug S --function F --diff` needs no ROM path or offsets (it takes the target, the
project header and the function's own compiler from the app) and prints the app's differing rows after the score (the first 60; `--rows all` or `--rows N` with it for more); in a
loop, reuse one `Judge(slug, name)` worker (`.score(source)`, `.judge(source, diff=True)`).
**Do not search on an approximation**: a home-made scorer (opcode alignment plus register counts) ranked candidates
wrongly: a 1000 → 680 local improvement was 1090 → 1030 in the app. Confirm any result once with `compile_and_diff`.

Before searching, reproduce the live baseline with the same source, effective compiler, flags and
project context. Resolve a score discrepancy before ranking variants. Measure children against the
seed they came from and keep the earlier control so the effect of a questionable edit stays visible.

### Shared types: put a real game structure in the project's header

A type you worked out for a structure that more than one function uses (a Gfx-like command record, the one-member
record around a global, `Vec3`, a record layout from `guess_struct`) belongs in the shared header, so later claims and
m2c/`redraft` drafts start with it; a type only this function needs stays in your candidate (a header edit is
re-checked against every matched function, so don't spend it on a local helper). `shared_types.py list SLUG` shows what the
header declares; write the definitions to a file and `shared_types.py add SLUG TYPE.h` checks them and prints the proposal
(it refuses a name the header already declares, identical or different, a name a macro or variable uses, and members of
an undefined type); `--apply` appends it with the app's `edit_context` (`edit_file` with `--file PATH` for a public-decomp
project). The app then re-compiles every matched function with the new header and rejects the edit, naming them, if
any stops matching: adding a type must not break anyone's match, and the app is the judge of that, not this script.

### Large functions (over ~1000 instructions): one region at a time

`regions.py FILE.c --slug S --function F [--size 40] [--show N]` splits the diff at the target's basic blocks into
regions of at least 40 instructions, with each region's differing rows by kind and an estimated score, marks the
regions that already match and the first one to fix; `--show N` prints one region's rows.
1. **Fix the frame first.** A frame size that differs (`addiu $sp` and every `s` row) shifts every region; get region
   0's prologue and the stack slots right (locals' count, size and order, spill slots) before anything else.
2. **Then region by region from the top**: the first region that still differs, until it reads "match", then the
   next. A fix above shifts registers and order below it, so don't polish a later region while an earlier one
   differs; re-run `regions.py` after each accepted change and check that no matched region broke.
3. **Prove each region's family**: when an edit makes a region match, say which library family (or which missing
   one) does it (`prove_chain.py` on the before/after, step 4), so the large function's fixes become permuter steps.

## 3. What has worked (extend this list when you learn more; it is the permuter's to-do list)

**Search the recorded evidence by symptom before reading this list:** `python tools/learnings.py search "register swap"` (also "delay slot",
"store too early", "frame size", "hoisted constant", ...) ranks every `docs/learnings/` entry and the older
`COMPILER_MATCHING_LEARNINGS.md` sections by those words (with synonyms) and prints each one's title, the paragraph that
says the fix and the library families it names; try those families with `prove_chain.py --families`.

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
- **A pointer set at several arm heads sits first in every block; one pointer per arm sits beside its use.** A table
  address that the target loads with a `lui $s0` at the entry and at each arm head (`lui $a2; lh $a2; li $a0; move $a3;
  sw $s0; lui $s0` in the first block) is a single-set pseudo in each arm: the first scheduler places a pseudo's only set
  late. One `base` variable assigned at every arm head is a multi-set pseudo and its `lui` comes first in each block (the
  one remaining row difference, 1140 in a save-slot state machine). Write the continuation the arms jump to out in each
  arm, each with a pointer of its own; cross-jumping after allocation merges the identical tails again (1140 -> 0).
  decomp-repair `goto_run_copy` (0.5.604) copies a label's run per goto and splits the pointer by assignment web.
- **A store that should sit between two loads of a pointer chain, and a float add with its operands' registers
  swapped** (KMC 2.7.2, both read in the source): the insn after a loop note depends on everything before it
  (`sched.c`), so end the opening `do { } while (0)` between the two loads, the copy whose store belongs between them
  last inside; and `flow.c` weights references by loop depth while `local-alloc.c` breaks equal priorities by birth
  order, so stage the product and put the add (with a neighbouring copy) in a scope of its own. 70 -> 10 -> 0 on an
  851-instruction ray test; decomp-repair `entry_scope_split` (0.5.614).
- **`local_flow` declined on any `volatile` in the text** until 0.5.604, so a saved best with `*(volatile T *)&global`
  reads had no variants from the ~40 flow-based families. Only a volatile local still declines; check
  `local_flow.analyze(source, name)` is not None before concluding the permuter has nothing.
- **A scratch local assigned in every arm of a switch (registers only differ, `packet`/word pairs in `$a0`/`$v1` swapped
  in all of them).** One top-level name for the cursor and the command words is one global pseudo with a huge reference
  count, allocated before the short per-arm pseudos the target has; one local per arm gets the target's registers, but
  each arm alone is worse (its tail stops cross-jumping with the others'), so all 24 webs go at once (365 -> 0).
  decomp-repair `local_web_split` offers it as one child; a chain of one-arm children never finds it
  (docs/learnings/function/display-list-arm-locals.md).
- **A copy `move rX,rA; addu rX,rX,rIdx` in a loop that every spelling of the store folds into one `addu`** (about 400
  tried: casts, helpers, pointer locals): cse leaves `q = base` but combine folds it into its add only inside one basic
  block (flow.c's LOG_LINK) and cross-jumping runs after allocation, so write the add in both arms of an identical-arm
  `if (g) { q += i; } else { q += i; }` on a long-lived local the loop does not assign: the copy stays and jump2 merges the
  arms. A guard on a pointer the loop increments lifts its priority and swaps registers (30); a long-lived one gave 0
  (230 -> 0 on a 300-instruction text-entry handler). decomp-repair `store_copy_arm_split`; `fast_judge.py --flags` probes
  a profile (`-fno-strength-reduce`) without changing the function's.
- **A saved best full of search scaffolding (union around a pointer and an integer, `if (x) { S } else { S }`, identity
  inline, copies of one cast, word locals every use truncates) is a dead end for a greedy chain**: each removal is neutral or
  worse alone (145 -> 240 on a 129-instruction list insertion, then 240 -> 0 with a loop key copy). decomp-repair
  `scaffold_strip` writes them out together and offers the child with `loop_invariant_key_copy` (`scaffold_strip_key_copy`);
  if `prove_chain` finds nothing on such a best, check that family fires first, then rewrite by hand and chain from there.
- **Frame slots: an address-taken scalar is allocated at its first `&`, an array when its scope opens.** Five `s` rows
  with the scalar's slot below the arrays' in the target mean the arrays belonged to a block after the call: declare
  them in a block opened after it, in the target's order (no `volatile` needed). decomp-repair `late_aggregate_scope`;
  the m2c `(flag = p != NULL, a) || (flag = p != NULL, b)` then `flag && q` guard sinks with `boolean_guard_operands`
  (220 -> 5 -> 0 on a 418-instruction function; docs/learnings/finding/address-taken-scalar-slot-after-block-arrays.md).
- **A `switch` whose arms meet at an m2c `block_N:` label (`case A: v = 1; block_4: if (t == v) { case 6: ... return 1; } return 0; case B: v = 2; goto block_4;`).**
  The compiler lays the block directly after the arm that falls into it, so which arm holds it, and the arms' order, are
  bytes (570 -> 0 on a 31-instruction predicate); no whole-arm reorder moves the block. `switch_join_host` moves it and
  tries the orders (the target's jump table, `tables`, names the host arm); `switch_join_values` exchanges the constants of
  two arms that reach one join. A **score of 1 with no differing rows** is the jump table's data: `fast_judge.py --diff` now
  prints "Linked jump-table differences" (case | target | compiled) and `prove_chain.py` hands them to `linked_switch_labels`
  (docs/learnings/finding/switch-join-block-host-and-order.md).
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
  Don't edit variants by hand: mark them in the source (`/*{*/a/*|*/b/*}*/` for alternatives, `/*permute{*/ ... /*}permute*/`
  around one statement per line) and run `grid.py FILE.c --slug S --function F` (`--count` first; it refuses over 5000
  candidates, runs 3 judge workers, prints the top scores, `--diff`/`--out` for the best; `--base` writes the unmarked file).
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

- **A loop whose every access is `lui $at,%hi(sym); addu $at,$at,$sN` (no pointer per table).** The target was built
  with `-fno-strength-reduce` (this project's per-function profile) and the source indexes by byte offset; under the
  default profile the same source is strength-reduced and every step scores worse, so profile and source must be
  changed together (cc1 `-dL` prints the givs "not worth while" alone and reduced once combined). Measure each step
  under the profile, not the default. decomp-repair `strided_table_loop` (0.5.363) has the nine steps and `probes()`
  the joint child; the notes in COMPILER_MATCHING_LEARNINGS.md give the order.
- **`do { } while (0)` adds loop-depth weight** to a variable's references at no instruction cost, which reorders
  register allocation. Wrapping a counter's start several times raises it; removing a wrapper also works (hypothesis:
  it double-counts the references).
- **Stray call arguments m2c leaves on one-argument callees** change sched1's order elsewhere: drop them all at once.
- **A copy-back (`B = A;` after `A = B;`) that emits nothing** can fix ordering by making a variable multi-set.
- **A table address the target loads fresh inside a guard, where every spelling gives a hoisted, spilled copy
  reloaded into `$s7`** (KMC 2.7.2): loop.c moves a constant's set unless it is a user variable set where the loop may
  not reach it (`maybe_never`) and used in a later block, and never moves registers made by an inner loop's
  optimisation. Set a pointer in the guard (`u32 *tp = T;`) and read the loop through it; one pointer assigned in
  several guards is never moved at all. `-dL`'s `done move-insn matches` lines show the merge that hoists it.
  decomp-repair `guard_symbol_pointer`, `guard_alias_pointer`, `stepped_cursor_index` (290 -> 230 on a bit-stream
  decoder; docs/learnings/function/bit-stream-decoder-guard-pointers-and-loop-made-registers.md).
- **Two arms building one word from a command's bytes, the bytes and the shared pointer one register round** (SN 2.8.1):
  anonymous byte reads are block-local and local-alloc colours them before global.c gives the arms' shared values theirs.
  Hold the reads in locals both arms assign (global pseudos), put each arm's packet pointer back in its own block, and
  give the arms one statement order (365 -> 0 together). decomp-repair `arm_operand_share`
  (docs/learnings/function/modify-vertex-arms-shared-byte-locals.md).
- **A goto loop stops loop.c hoisting constants** (do/while/for loops get the loop pass's treatment).
- **Copy a short block in place of a `goto` to it** to give a value an extra use (cross-jumping merges the copies back).
- **`const` pointer parameters** change the prologue's load order.
- **A struct of `volatile` fields in an m2c draft** is usually just spilled registers: make them plain locals.
- **Frame size: use loops, not scratch arrays.** Each pre-test `for` loop keeps 8 extra frame bytes in KMC GCC 2.7.2
  (hypothesis).
- **Leaf functions: temporaries in `$v0`/`$v1`/`$a0..`, decided by two allocators.** Pseudos that live in one basic block
  and die once go to local-alloc.c (by `floor_log2(refs) * refs * size / life`, a copy to `$v0` first), and it *ties* the
  output of an insn to a first operand that dies there (`and`'s result takes its first operand's register); pseudos that
  cross a block, or die twice, go to global.c (priority as in the `-dl -dg` table). A register exchange between two locals
  with no loops: count weighted references from the source (a reference is 1 plus its loop depth; the table agrees to the
  digit) and wrap the statements that name one and not the other in `do { } while (0);` (one more reference each),
  deeper until the order flips: decomp-repair `call_weight_ladder`, `local_weight_ladder` (85 -> 15 on a heap
  allocator). A complement (`~mask`) computed before a two-arm branch that only one arm reads is a global pseudo; sunk
  into the arm as its first statement in a scope it is a local that heads the arm and fills the branch's delay slot
  (`arm_invariant_sink`, 115 -> 85). Reusing one variable for a sum and its result gives it two deaths (global) and
  breaks the tie. The delay slot takes an arm's first one-word insn, or one from above the branch, never a later one.
  Correction (same allocator, 15 -> 0): reorg does take a later insn when nothing it skips is a `volatile` load; the
  complement after the sum keeps the mask live into the sum (no shared register), and a sum variable shared with a
  disjoint local of another block is global, so the `and` ties to the complement instead (`complement_late_plain_reads`,
  `disjoint_local_merge_swap`; docs/learnings/function/heap-bump-arm-complement-tie.md).
- **Identical arms the target keeps apart, and a shared pointer that swaps registers** (a tile lookup, KMC 2.7.2, 6175 -> 0;
  docs/learnings/finding/tile-lookup-arm-chains-and-crossjump.md). The final `jump_optimize` cross-jumps two jumps to one
  label whose preceding insns match (commutative ops in either operand order, same hard registers) all the way back to the
  conditional jump, *unless the label was made during that pass* (`jump_chain` partners are searched only for labels older
  than the pass): give each arm its own copy of the shared tail and the first tail merge makes the new label, so the arms'
  chains are never compared (`label_run_copy`). A pointer set in several arms (`goto L; L: return *p << 7;`) is a global
  pseudo, coloured after every arm's local quantities, which swaps `$v0`/`$v1` and also changes reorg's delay-slot choice
  for the `return 0` test (`bnel` with a private pointer, `bne` shared: `shared_return_split`). Make the index term the
  pointer operand (`(u8 *)(i * K) + (int)bank`) to tie the sum to it, put a `do { } while (0);` around the row product to
  outrank the index chain, and read a masked parameter through a `u8` copy (`tile_arm_chains`).
- **A byte copied into a scratch that cse then merges back (`temp = kind;` both `s32`), a counter incremented inside its own
  test, a loop bound that becomes `move $t0,$a2`, a narrow comparand's extension that comes out after the bound's load**
  (KMC 2.7.2, 655 -> 0 on a 154-instruction save-slot handler, docs/learnings/function/narrow-copy-web-save-slot-rank.md).
  A copy kept in a local of the byte's own type is another machine mode, so cse leaves it (`narrow_copy_web`); `if
  (temp++ != i) {..}` becomes `if (temp != i) {..} temp++;` so there is no copy of the old value
  (`postincrement_after_test`); a bound the target loads as `lw $t0,-0xc($t0)` off the register that holds another global's
  address is `limit = ((s32 *) &H)[-3];` as the do body's first statement (`sibling_symbol_offset_read`, evidence from the asm);
  and `idx = index;` before it puts loop.c's movables in the target's order (`loop_body_key_copy`).
- **m2c's join copy (`joined: check: near = force;`) that cse folds away, kept by an arm that copies for itself** (KMC 2.7.2, 640 -> 0
  on a 148-instruction object handler): a copy whose destination is first referenced at the join is deleted (`make_regs_eqv`); copy it
  in the arm that jumps to `check` (`near = force; goto check;`, `check:` after the join's copy) and put each arm's `force = 1;`
  after its early-`return` guard. Each alone is worse (700, 1150); decomp-repair `join_copy_arm_anchor_sink` offers the combination
  (docs/learnings/finding/join-copy-arm-anchor-and-flag-store-order.md).
- **Flag words and widths:** bit-field structs for flag words; `u8`/`u16` widths keep or drop masks; overwrite a
  parameter after copying it (`arg = arg_in; arg_in = 0;`).
- **A second pointer written beside the first (`q = p + 2`, `q[-1]`, `q[0]`, `q += 3` beside `p += 3`, or `q[i * 3 - 1]` for the
  loop counter) is the loop pass's own work, not the game's.** loop.c reduces every `p[k]` (constant `k`) to a pointer of
  its own and combines the offsets, and puts that pointer's increment just before the biv's; a user cursor orders its own
  steps differently and costs a register order. Write plain `p[1]`, `p[2]` with one `p += 3` where the cursor's step was.
  An opcode byte tested in a loop and switched on wants one `u8 op` local (`lbu $a0; andi $v1, $a0, 0xff` before the
  compare). A switch arm with a dead reload followed by an overwrite is unfaithful: give each loaded value a block-scoped
  `u8` local. decomp-repair `cursor_lockstep_fold`, `dispatch_byte_local`, `arm_reload_restore`
  (docs/learnings/function/interpreter-derived-cursors-and-opcode-local.md: 550 -> 0, seven chained steps).

- **A table walk whose per-iteration pointers (flag, name, code, raw record) are locals set at the top of the loop body: the
  preheader and increment order is the reverse of the order loop.c meets the givs.** A match at 195 with only those rows and
  three registers left was 0 once the pointer locals were written into each arm as the expression (`gTable[i].name`) and a
  one-use local was set right before each constant-offset store (`p[16] = 0`: `express_from` needs a literal additive part, so
  only a replaceable local is addressed `0x10($s1)`). The record typedef matters (`(char *) sym + K + i * 24` spellings were
  far worse). decomp-repair `loop_giv_local_arm_inline`
  (docs/learnings/finding/giv-locals-set-at-loop-top-vs-in-the-arms.md); `cc1 -dL` prints the giv list in emission order.

- **Volatile step cursors, `if (n > 0) do { } while`, and the loop's frame hole** (KMC 2.7.2, 922 -> 0 on a 529-instruction
  function, docs/learnings/function/volatile-cursors-to-guarded-loops.md). Stepped `volatile` locals are the loop pass's own
  givs (`i * 3`, `i * 6`), spilled: write them off the counter, and the spill area's slots then fall where the target has
  them (the 8-byte hole is the dropped mult-2 giv; a `for`'s copied entry test leaves stale pseudos that take slots too).
  The counter must out-rank the argument copy (`floor_log2 (refs) * refs / live`, refs weighted by loop depth): a `for`
  with the plain bound did it, a `do { } while (0)` around its start raised the givs as well. Library: `volatile_step_for_loop`,
  `counter_offset_local`, `guarded_cursor_do_loop`, `load_add_in_place`, `sequential_cursor_split`, `scope_unwrap_rewrap`,
  `adjacent_init_swap`; `prove_chain` reaches 0 from the saved best with them.

- **Typed one-member field reads that sit too early, two neighbours the scheduler swaps, and a block of exactly three
  local quantities** (KMC 2.7.2, a 603-instruction state machine, 1695 -> 0; docs/learnings/function/state-machine-volatile-pin-and-plain-field-reads.md,
  docs/learnings/finding/local-alloc-three-quantity-block-sort.md). `((Rec *) p)->value` reads hoist above a store to a scalar
  global (sched.c `true_dependence` treats the member load as a different object): write all of them as plain casts
  (`record_member_plain`, 1370 -> 415). Two accesses through one base are free to swap unless both are `volatile`: mark exactly the
  two (`volatile_access_pin`, 120 -> 60 -> 0). A block with exactly three local quantities is sorted by a buggy exchange in
  local-alloc.c, so the spelling that makes one of them global (the mask written into the shared result local,
  `rmw_result_local`) flips the allocation. One scratch local reused by two goto ladders gets a name per ladder
  (`region_local_split`, 1675 -> 1370); `a = b = c = 0.0f` instead of a zero local in a `do { } while (0)` fence
  (`float_literal_chain`); m2c's mask-and-shift word updates as `p->field |= K` on a bit-field typedef (`field_rmw_bitfield`).

- **A frame 8 bytes larger than the target's with every spill at the target's offset: look for a table read twice as bytes.**
  Two straight-line reads of one symbol through byte arithmetic (`FIELD(table, s32, e * 24)`, `*(T *)(table + e * 12)`,
  `table[i * 24]`) leave a stale address-sum pseudo that reload gives an unread slot; the byte table counted in a loop also
  has its `la` hoisted into a register the target doesn't spend. Typed rows (`Row table[]`, `table[e].f0`; a loop's table
  `u8 table[][24]`, `table[i][0]`) fix both (2028 -> 1015 on a 445-instruction picker). decomp-repair `record_slot_rows`
  (docs/learnings/finding/byte-arithmetic-table-reads-leave-a-frame-hole.md). Check `.frame ... vars=` in cc1's output before
  chasing the frame by hand.
- **Hoisted invariants (a literal compared twice, three symbol pointers) take registers in pseudo-creation order, ranked by
  references over live length.** A literal held in a counter the function has finished with (`n = 3;` before the pointers,
  which are assigned afterwards) takes the counter's high-priority register and is emitted first; the same literal as a new
  local ranks last. A product written back into one of its operands (`d = call() * d`) has the destination's register first
  in the `mul.s`; write it to another float that is free there. decomp-repair `loop_constant_holder`,
  `float_product_retarget` (685 -> 0 together); `guard_load_split` (the load before a float guard's last test plus the loop
  bound assigned after the guard, 1015 -> 705 only together) and `switch_join_condition` (705 -> 685).
- **A switch whose arms `goto` labels inside it (m2c's `goto set_kind_flags`), a parameter that loses a saved register by one
  priority step, and four small edits that are each worse alone** (KMC 2.7.2, a 713-instruction object spawner, 2420 -> 0;
  docs/learnings/function/spawner-label-arms-and-flag-scope-2420-to-0.md). The shared `var_v1`/`kind_mask` locals of the
  merged tail are function-wide pseudos that global.c ranks first; each arm needs its own copy of the run with block-local
  scratch values, literals folded and the word update as `v = X; v &= M; v |= K; <the arm's earlier statements> X = v;`
  (`switch_label_arms`, `_mixed`). A parameter read once for a flag test (`if ((a15 << 0x18) != 0)`) is 0.071 against a
  parameter the arms read five times (0.083): compute the flag in `do { f = a15 << 0x18; } while (0);` and test `f`
  outside it (`parameter_test_scope`; the scope around the whole `if`/`else` turns the arms through jump.c's "if (foo)
  bar; else break;" rule). Also `narrow_return_compare` (a `volatile short` callee widened and `3 < (x + 1)` as
  `x >= 3` together), `switch_range_trim` (the aliases of `default:` outside the target's `addiu`/`sltiu` range) and
  `volatile_cast_unpin` (a volatile load cannot fill a delay slot, and the `nop` moves every later table entry).

- **A display-list builder whose draft has two pointer chains (`var_t0 = var_t1 - 4`, `temp_t0 = var_t0 + 8`, `M2C_FIELD(temp_t0, s32 *, -4)` /
  `(prev, s32 *, 8)`) and a cursor advanced by `+ 0x38` / `+ 0x18` per arm** (KMC 2.7.2, a 369-instruction N64 sprite-tile
  builder, 10891 after 340 attempts -> 0): that is the loop pass's own reduction of one cursor written as the SDK macros on
  `Gfx *_g = gfx++`; write the macros (`gDPSetTile`, `gSPTextureRectangle`, ... on `gfx++`, the format field as the argument
  itself, the rectangle whole in each arm with its operands `a * 4`, `b * 4`, `s * 4`, `t * 4` as locals before the hi-res
  branch) and gcc gives back the second register, the -4/0 stores and the chunked increments. A hand-written
  `g->w0 = ..; g->w1 = ..; g++` without the `_g` copy gives one register and a worse score. decomp-repair
  `gbi_loop_cursor` writes the macros and `hoisted_invariant_inline` the invariants back (docs/learnings/function/display-list-local-cursor-macros.md);
  the clamp order, signed `x1`/`y1`, in-place operands and the `for` header after them are still by hand.
- **A cursor walk that kept an extra saved register (`$fp`), a byte copy that must survive in one arm, a global read that
  must stay a temporary** (KMC 2.7.2, a 384-instruction sound-entity starter, 2696 -> 0, nine library families;
  docs/learnings/function/sound-entity-starter-narrow-copy-and-counter-forms.md). loop.c reduces a pointer stepped beside a
  counter into a giv of its own when two or more address uses exist, so the cursor and the parameter it started from take two
  registers; write the walk as `(arg + i * 8)` with the counter stepped last and the parameter keeps its register
  (`walk_counter_for`). One counter variable per width gives the allocator one pseudo with every loop's references
  (`loop_counter_unify`). A byte read into a wide local and read in both arms of an `if` shares one address sum through the
  branch's delay slot; a narrow (`u8`) copy that the first arm reads is another machine mode, so cse keeps it and combine
  makes it a `move` (`arm_byte_copy_split`). `t = G; G = f(t); v = t;` keeps the copy after the store where `v = G;` folds it
  (`global_read_store_copy`). A saved register's value read once more after a call-free loop wants a copy before the loop
  (`post_loop_copy`); a call argument m2c rotated one iteration ahead is `f(i & 0xFF)` (`loop_argument_inline`); a store
  address staged in a local with the value's `|` chain left-associated (`store_address_stage`) and a literal local above the
  loop's initialisations (`loop_literal_local`) each pay off only with their partner edit. `reach.py`-style check: the app's
  proposal code offers every child of the state before an edit, and the edit is a gap when none of them moves the score.

- **A 795-instruction object spawner, 3693 -> 0 (KMC 2.7.2; docs/learnings/function/object-spawner-arm-and-join-rewrites.md).** A clean
  rewrite from the target's assembly, then one named edit at a time (each now a library family): constants an arm sets after its calls
  assigned at the arm's head so they cross the calls and take saved registers (`arm_constants_to_head`); a call after the
  `if`/`else` written in each arm so jump2 cross-jumps the two `jal`s (`call_into_each_arm`); a pointer local assigned twice split
  per lookup (`local_pointer_web_split`); `local + 1` named once (`shared_sum_local`); an `s16` parameter's masks as `(u16) id`,
  which stops cse sharing the extension and lets combine fold the index shifts (`narrow_param_mask_cast`); a call's result written
  through its field (`result_field_reread`); a word update as `w = F; w &= M; w |= K; F = w;` with a temporary per site
  (`rmw_compound_temp`); and a table slot staged `ex = (s16) id; tab = T; p = &tab[ex];` so the `la` sits between the index's
  halves (`table_base_stage`). Sched/regalloc rows near the end were closed by `grid.py --by-rows` and a random hill-climb over
  statement order and spelling alternatives (ranked by differing rows: 635 -> 180 -> 120 -> 60 -> 0), not by the score. A parameter
  copied in a different order at entry means the original parameter was narrow (`s16`): its conversion insn is emitted last.

- **Two loads in the wrong order in one block, every statement order the same: the one that is set once is boosted.**
  `sched.c` schedules backwards and launches an insn whose destination pseudo is set exactly once
  (`birthing_insn_p`, the `7f000001` priority in the `;;` lines of the `-dS` dump) as soon as it is ready. A masked update
  written `p->w = (u32)((p->w & M) | O)` loads into such a pseudo and goes ahead of a float load into a register that has
  several sets; written as a local updated by separate statements (`w = p->w; w &= M; w |= O; p->w = w;`, the local declared
  one place above the last) the load is not boosted and the target's order comes (60 -> 0, decomp-repair
  `rmw_user_local_steps`; docs/learnings/function/player-update-routine-chain.md). A 25-line program with the same few
  statements reproduces the order in a second: do that before spelling variants on the real function.

- **A 1133-instruction effect spawner, 7905 -> 2930 in nine steps (KMC 2.7.2; docs/learnings/function/effect-spawner-switch-slot-counter-goto-loop.md).**
  m2c's `if (n != 2) { if (n >= 3) { if (n < 5) B } } else if (flag) A1 else A2` is a `switch (n)` (the dispatch is
  `beq`, `slti`, `slti` with the single-value arm first: `embedded_if_tree_switch`); a record member read twice in a loop
  block is a pointer local at the head of that block, inside the `if` (`member_slot_pointer_local`: above the `if` it
  becomes the loop's induction variable); a constant word store where the target has `sb` is `u8` (`narrow_store_width`);
  a literal stored after a `(u32) f` conversion is a local set before the float arithmetic, stored first
  (`constant_store_early_local`); a later loop counted with the first loop's counter, read after it, shares one pseudo
  (`loop_counter_reassign`); a call result tested and used throughout the arm is copied at the arm's head
  (`call_result_arm_local`); a `do { } while` whose literals the target does not hoist is a goto loop (`loop_goto_form`,
  4035 -> 3555); and a local update (`x += K;`) goes below a statement with a call that does not name it
  (`statement_slide` past a call). A hill-climb that swaps adjacent statements must check that they commute: one
  accepted a read before its set (60 points "better"). Run the library's children over **all** of them (`prove_chain`
  stops at `--children 80` of ~2700): a chain of the best child, scored in full, found 5 more steps (-1900).
  Continued 2930 -> 70 (docs/learnings/function/effect-spawner-loop-index-forms-2930-to-70.md): m2c's cursors back to the
  index loop (`markers[i]`, `base + 0x438 + i * 16`, the call's arguments inline); a loop's computed table index named
  in a local so the symbol is not hoisted (`loop_table_index_local`, 1875 -> 810); a stray argument on a callee the
  project renamed (signatures.py reads old address names now); a double kept in `$s4/$s5` as a literal (GAS `li.d`:
  `lui $at; lw; lw`, `double_literal_global`); a flags update as a local in steps (`rmw_user_local_steps` on m2c's
  cast form, 470 -> 185); parallel `D_ADDR` field tables as one record array (`record_field_array`); `<< k` for scaled
  record offsets (`index_shift_spelling`); a no-op copy-back dropped (`copy_back_drop`); a shared temporary holding
  the word with the mask at its use (`temp_operand_stage`); a constant set apart from its copy, since cse swaps an
  adjacent `A = K; B = A;` when B lives longer (`statement_slide`, docs/learnings/finding/cse-swaps-a-constant-into-its-adjacent-copy.md). Left: two float pairs whose
  copies the target keeps in the switch arms though never read, and local-alloc's `optimize_reg_copy_1` comparing a
  clamp on its copy.

- **SN64 GCC 2.8.1: two pointer tables read with one index in a loop, the wrong table's address hoisted** (the target's
  rematerialised `lui/addiu $t0` is the other table): loop.c moves an address only when `threshold * savings * life >=
  insn_count`, and `&T[m]` lives 2 insns (never desirable). A user pointer `tt = T;` at the body's top is the one moved;
  then one slot pointer per table, loads after both slots (`ts = &tt[m]; vs = &V[m]; tri = &(*ts)[k]; vtx = *vs;`):
  55 -> 0 and 195 -> 0 on two triangle walkers; decomp-repair `loop_table_slot_pointers`
  (docs/learnings/finding/loop-hoists-the-table-a-user-pointer-names.md).
- **A display-list texture loader whose target ORs registers holding 0 into a SetTile word (`move $s5, $zero` ...
  `or $v0, $s5, $fp`), or whose mip loop keeps zero copies of its counter, with a frame 8 bytes larger than ours:** the
  SDK's gDPSetTile clamp fields as locals, `cmt = cms = 0;` set once at the top and `((cmt & 3) << 18)`,
  `((cms & 3) << 8)` written into every `0xF5...` command's word. cse folds them only inside the block that follows the
  set, so a join (the `x < 0x800 ? x : 0x7FF` clamp) and the loop's hoisted invariants keep the registers. Look for a
  matched sibling loader first: its spelling (`G`, `CALC_DXT`, the ternary clamps) was the rest (7532 -> 0 with four
  statement-order grids; decomp-repair `settile_clamp_locals`, docs/learnings/function/texture-loader-settile-clamp-locals.md).
- **SN64 GCC 2.8.1: a loop local stepped by a constant (`y = 0x37; ... y += 0xE` in both arms) whose `li` sits first in
  the preheader where the target has it after loop.c's pointers:** write it as a giv of a zero-based row counter,
  `y = row * 14 + 0x37;` at the top of the body and `row++;` before the counter's step (bivs are reduced newest first),
  60 -> 0; decomp-repair `stepped_constant_row_giv` (docs/learnings/finding/stepped-constant-local-li-first-in-preheader.md).
- **A `move` the target keeps before a value's uses (a count copied for an allocation size), every plain copy merged
  away:** cse2 rewrites a block-local copy's uses to its source and local-alloc ties it; a variable the later loop uses
  (its counter, or a byte offset stepped beside it) lives across blocks and stays apart: `loop_offset_copy_reuse`
  (150 -> 0, docs/learnings/finding/count-copy-kept-by-loop-variable.md).
- **`if (G == 1)` with the load and the constant in each other's registers, three local quantities in the block:**
  local-alloc's fixed three-way exchange lets the short-lived constant win; load `G` into a local in a two-deep
  `do { } while (0)` (`compare_load_weight`, 15 -> 0, docs/learnings/finding/three-quantity-block-constant-outranks-load.md).
- **SN64 GCC 2.8.1: a literal the target loads once, late, in the register reload uses (`li $t6` beside `mflo $t6`), with
  the block's other values one register round:** the original's constant got no register at all. One local per field
  (`r = g = b = a = 0xFF;` as four statements at the entry), each a low-priority global pseudo live across the calls, finds
  the saved registers taken and caller-save unprofitable, so reload rematerialises it (470 -> 82; with the vertices
  written field by field 0). decomp-repair `constant_channel_locals`
  (docs/learnings/function/display-list-channel-constant-locals.md). A leaf has no such path.
- **SN profiles (sn-n64-281, 2.7.2):** `passes.py ... --save DIR` is the one command (see Tool gotchas). By hand it is
  `cppn64.exe -P -undef -nostdinc -D_LANGUAGE_C -D__GNUC__=2` then `cc1n64.exe -quiet <flags> -da FILE.i -o FILE.s`, both under
  wibo; the dumps land beside the `.i` as `FILE.i.lreg` etc. (consoles/n64/sn64.py has the exact commands).

## 3a. The fast tools (use them before writing variants by hand)

- **Read the `levels` line first.** `plain_best.py` judges the saved best at each other -O level. When one is far closer
  (`closer under ... -O1`), the function was built at that level: write plain C from the target (typed records, a real
  `switch`, no fences) and judge it there; do not tune the old best.
- **A claim starts with several sources.** `setup.py` prints the saved best, its plain form (`plain_best.py`:
  decomprepair `scaffold_strip.plain`, fences opened and scaffolding stripped) and the levels of decomprepair
  `clean_rewrite` (`clean_rewrite_structure.c`: m2c's gotos as loops, `if`s, `break`s and returns;
  `clean_rewrite_scaffold.c`: also inline helpers, `__repair_` literals and m2c's deref spelling written out;
  `clean_rewrite.c`: also field views, fences and `scaffold_strip`) with their isolated scores, in
  `/tmp/dd-HOLDER/start/`. When prove_chain stalls on the best, chain from the others. A clean level rarely scores
  below the best (the search tuned the best's spelling) but it is the readable start for a rewrite by hand
  (docs/learnings/experiment/clean-rewrite-start.md).
- **Matched neighbours.** `setup.py` also prints `neighbours.py --slug S --function F`: the closest matched functions
  (callers, callees, the functions beside it in its file, ones reading the same globals, look-alikes) with the start
  of their matching C. Their temporaries, loop shapes and prototypes are the cheapest evidence there is; helpers get
  the same section in their brief. Given the saved best (setup.py passes it) or `--source CAND.c`, it also lists
  matched functions whose assembly holds the *mismatched region's* instruction shape (the same clamp, cursor write
  or helper idiom), with their C around that place: someone already found the C for that idiom.
- **Call signatures: `signatures.py --slug S --function F [CAND.c]`** (`setup.py` prints it for the saved best). Each
  call is checked against the callee's matched C, the header's prototype and (MIPS) the target's call site: an
  argument the callee never takes, a missing one, a narrow return type the header gets wrong, a result the target
  reads that the C ignores (or the reverse), and the function's own parameters against its matched callers. Make
  those edits first: a stray m2c argument moved a display-list builder's cursor sums (55 -> 0 by dropping it) after
  twelve spellings of the code around it compiled identically.
- **Edits that are each worse alone: `prove_chain.py ... --combine 8`.** A stalled step then ranks the non-improving
  children by differing rows, merges pairs and triples of different families (decomprepair `compose`), and judges the
  children of the best three (`--lookahead`). It only combines what the library offers: when it finds nothing, check
  whether each half of your combination is a child at all (`--list`); a missing half is a family to write, not a
  search to widen (docs/learnings/experiment/combined-step-compose-lookahead.md).
  With `--combine` it also demotes children that leave one more value to global.c than their parent (a block-local
  value given a second death: the dead-end shape of the retired `volatile_rmw_counter_share`); `--no-dead-end` skips
  that check (one diagnostic compile per ranked child).
- **Register rows: `explain_regs.py CAND.c --slug S --function F`.** For each register pair the rows swap it names the
  pseudo in the candidate's register and the one in the target's (refs, live, priority, block-local or global, deaths,
  copy preferences, C lines) and the rule that decided it (a hard conflict, a pseudo allocated first, a lower-priority
  pseudo's preference skipped by find_reg, its own copy preference, local-alloc's block order), then `cause:` lines
  naming the instruction that made each conflict or preference, its kind and C line ("call-argument copy: ... into
  $a1 for the call to helper, argument 2, C line 5"). Read it before reordering declarations; a call-argument cause
  sends you to that call (its arguments, signatures.py), not to the code around it.
- **Many register rows in a large leaf: `alloc_roles.py CAND.c --slug S --function F --whatif 8`.** It reads, for every
  pseudo, the register the target has where ours has the pseudo's (its role), replays global.c's colouring from the greg
  dump (it reproduced 84 of 85 allocnos on a 681-instruction decoder) and names the priority changes that would put the
  misplaced ones in their target registers. An inner loop's temporary shown in a long-lived variable's target register
  was that variable in the original: write it as that variable (decomp-repair `block_temp_role_merge`; 1240 -> 585 on
  the decoder, docs/learnings/function/bit-stream-decoder-frame-slot-and-allocation-simulator.md). Apply role edits
  together and grid their subsets: one at a time they usually score worse.

## 3b. Out of hand-written shapes? Trace the compiler instead of guessing

Three or four structurally different sources landing on the same score (or the same missing/extra instruction) means
the shape is decided by a compiler pass, not by how you spell the C. Stop writing variants and find the pass.

**Invariant spellings mean the cause is elsewhere.** When distinct spellings of one block compile to *identical
instructions* (grid.py and fast_judge.py with several files print a `hint:` line when three do), the compiler
normalises that block: no fourth spelling moves it. Look at what feeds or consumes it instead: the call arguments
next to it (`signatures.py`: a stray argument decided a cursor block twelve spellings could not), values live across
it (`explain_regs.py` `cause:` lines), the matched neighbours' C for the same idiom, and the statements just before
and after it.

**Routine: read the dumps at 20 minutes.** After about 20 minutes on a function whose remaining rows are register or
order rows, run `dumps.py BEST.c --slug S --function F` once before any more spelling search: it prints
explain_regs.py's register pairs with their `cause:` lines, greg's conflicts and preferences, and both schedulers'
orders for the same source. Act on the cause it names (a call argument, a copy, a value live across a call) first.

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
   must write. The app installs each free compiler's source with it, in the toolchains folder as `<id>-source`
   (`toolchains.source_for("<compiler>")` gives the path, and `compiler_passes` prints it); IDO, Metrowerks, SN's own
   builds and Visual C++ have none. That one shared folder (macOS `~/Library/Application Support/decompnet/toolchains`,
   Linux `~/.local/share/decompnet/toolchains`) is the only copy: read it there, read-only. Never install a scratch copy;
   if a source you need is missing, say so in your report instead. Don't search the whole filesystem for
   `sched.c`/`loop.c`/`cse.c`.
3. **Turn the finding into a rewrite.** The pass's rule is the permuter's guide: a family in decomp-repair that
   produces the source shape the rule needs (step 5). Record the rule, not only the spelling, in
   a `docs/learnings/` finding ("jump.c inverts `if (x) goto A; goto B;` unless an insn sits between them" is
   reusable; "this function needed `goto`" is not).

4. **Register-only rows: dump priorities, not passes.** `passes.py CAND.c --slug S --function F` prints the allocation
   priority table (refs, live length, priority, register), each pseudo's register preference and both schedulers'
   instruction order with the project's compiler and flags, and where the compiler's source is; compare with the
   target's register order and change uses/live ranges (above, in 3). The raw `-dl -dg` dump is the same thing by hand. To run cc1 by hand, get the `.i` from `preprocess.py CAND.c --slug S --function F -o CAND.i`: it preprocesses exactly as the app does (the function's flags, includes and the project's header) and prints the cc1 command line to add `-dl -dg` to. Never run the bundled `cpp`/`cccp` yourself and never hand-expand macros: GCC 2.7.2's own predefined-macro list is malformed, so the bare program aborts (exit 134) on every file, even `int f(void){return 1;}`; only the `gcc` driver (which passes `-undef` and its own defines) works. `passes.py` does all of it for you. It also lists the hard registers each pseudo is live with (`;; N conflicts:` minus the pseudos): a register missing there is free for it, so a pseudo that lands on another one lost it to a *copy* preference (a plain move to an argument register, global.c `find_reg`; a `subreg`/extend source is only a plain preference and loses to a lower-numbered register: a `u8` result local took `$v0` instead of `$a3` that way). `passes.py --sched-verbose` prints the first scheduler's launch priorities and set-count notes: no patching compiler_diagnostics.py. An empty `.lreg` means cc1 didn't get the
   flag or failed to parse: preprocess with `preprocess.py` first.

**Stuck near 0:** no long blind or random searches. Decode the rows, read the cc1 dumps (`-dl -dg` allocation
priorities and preferences, `-dS -dR` scheduling), and run the recent repair families on the best first.

## 4. Test one repair at a time

1. Name the mismatch and the repair family that could change it. Use the function's saved Deep
   Permuter results to see whether the family already ran and what it emitted.
2. Run that family directly on the saved seed and score only the variants that test the hypothesis.
   Start with a small batch and score serially through one local judge worker. If using MCP
   `try_local_repairs`, pass exactly one family and a small explicit `max_trials`. A call with no
   family selects repairs automatically.
3. Compare the exact diff to the seed. Use `compile_and_diff` to confirm a measured
   improvement or exact candidate. Score ties and regressions in the isolated judge to preserve the
   live claim's compile budget.
   If the family misses the shape or routing skips it, prove the missing behavior with a regression
   and land the fix in step 5. When results plateau, read the diff and choose a new hypothesis.

`prove_chain.py --slug S --function F [SOURCE.c] --families a,b|all` applies the best-scoring child of those families
(including the diff-gated ones the Deep Permuter calls with the judge's rows) step by step (bounded: one judge, capped children and steps; `--list` shows which families produce children) and prints
each step's family and score: the cheap answer to "would the library have reached this from here". When edits are each
worse alone, `--combine 10` merges a stalled step's 10 best children in pairs and triples (off by default: it found
none on three three-edit functions, whose edits ranked far below the 10 best; narrow `--families` first).
The Deep Permuter does the same after its operand pass (`Search.repair_pair_pass`, `app/pair_search.py`: the best 30 children
from different families composed in pairs, judged by a pool of two workers).

Never start or wait on a Deep Permuter run, a sweep, `scripts/scratch_run.py`, or an equivalent broad search,
even for one function, and leave the running jobs' settings alone. Test a repair by calling it directly and scoring
its children with `fast_judge`.

## 5. Teach the permuter (do it first, merge it, then continue hunting)

For each manual step that moved the score, answer: **could the permuter have done this?**

- Yes, but it didn't: find out why (cap, ordering, budget, a rewrite that never applies to this shape) and fix it.
- No: write the rewrite or the search guide. Smallest useful form: a function in decomp-repair that takes source
  and returns variants, with a test that fails without it (see `call_scope_repairs.py` for the shape). Prove it by
  running it on the function you just matched: starting from the state *before* your fix, it must reach the fix.
- **Every step the permuter couldn't find becomes a decomp-repair family that declares itself** with a `REPAIR` dict at
  the end of its module (`REPAIR = {'families': FAMILIES, 'variants': variants, 'needs': ('source', 'name', 'header')}`,
  optional `order`, `lanes`, `archs`; `decomprepair/registry.py`). The app runs every declared module in the draft repair
  path, the Deep Permuter's beam and its local lane (`app/library_registry.py`) and lists its families for the agents, so
  **there is no app wiring to write**: the library PR is enough (the library's `tests/test_registry.py` fails for a module
  with `variants` and no `REPAIR`). `needs` are `variants` parameters by name: `source`, `name`, `header`, `arch`, `asm`,
  `flags`. Offer steps that only pay off together as one combined child. Before releasing, check the family returns
  `[(family, source)]` (or `[source]`) and prove the chain from the saved best where possible. A docs entry alone doesn't
  finish the goal. The ~700 older modules are still wired by hand in `app/draft_repairs.py` and `deep_permuter.py`
  (`tests/registry_legacy.txt` in the library); change those calls only to fix their behaviour.
- **Label compiler explanations not confirmed in the compiler source as hypotheses**, in docstrings and docs.
- Test the specific repair on the saved source and, when relevant, the draft. The saved source can
  spell the same operation differently from m2c. Add a unit test, verify that the permuter lane
  offers the candidate, and use the exact judge to measure it. After merging, check the live verdict
  with `get_function(section="summary")`. Describe the isolated evidence in the PR.
- Record the evidence as its own file: `python tools/learnings.py new function|finding|experiment NAME --compiler "..."`
  (`docs/learnings/`; never append to `docs/COMPILER_MATCHING_LEARNINGS.md`, whose shared edits made PRs conflict): exact
  compiler and flags, the source change, the measured before/after, and failed experiments. Say which are controlled
  results and which are one function.
- A change here needs its PR (AGENTS.md): branch `<type>/<name>`, test, `uv run ruff check`, merged after creation.
  Say in the PR which features you ran and how. **Never tag the library or bump its pin yourself.** Merge the library PR
  with no version bump and no tag; its release workflow tags the merge (it runs on every merge, and `gh workflow run
  release -R RetroReversing/decomp-repair` starts it at once), and the library-pin workflow here opens and merges the pin
  PR (`gh workflow run library-pin`); once the pin is on origin/main the app runs the module by itself
  (`git fetch -q origin && git show origin/main:pyproject.toml | grep decomprepair`). Run the module in the app's own
  paths from your worktree after `setup.py sync`. While CI runs, keep the next independent local experiment ready
  without changing a worktree used by an active judge. A run's "No module named decomprepair.<x>" means the pinned
  release predates your module.
- After the first 0, read `get_function(section="summary")` again and verify the function is recorded as matched
  with a best score of 0. If it still shows open, check the compile result before resubmitting.

Step 5 is merged *before* you continue hunting, not saved for the end. If you still cannot match the function
afterwards, leave its note with the evidenced blocker.

## 5b. Mistakes this method has already made (each cost a run or an hour)

- **Decode the target's own assembly before writing shapes.** The project's `asm/nonmatchings/**/FUNC.s` has the
  offsets and delay slots; the short mismatch summary may omit them. Reading delay slots (`bnez` with `li` in its slot, a `j` whose
  slot is the other path's value) gave the branch *polarity* in minutes after an hour of spelling variants that all
  had it backwards. Do this in step 1 whenever the diff is a layout difference, not only when stuck.
- **A search's own scaffolding is part of the diff.** `__repair_constant`-style locals and `s8 *` stores of constants
  above 0x7F (`li -1`, not `li 0xff`) look like m2c output and are not the game's: write literals, `u8` stores.
- **Never `git stash`, `git checkout` or `uv run` in a worktree whose venv a run uses.** `uv run` re-syncs the
  environment to whatever `pyproject.toml` says at that moment: a stash that reverted the pin silently downgraded
  the library, and a run failed at its first step with "No module named". Run `python -c "import decomprepair.<your module>"` in the run's own interpreter before starting.
  Resolve merge conflicts in a separate `--detach` worktree, not the one a run is using; never remove that worktree.
- **Merges and rebases: grep every changed file for conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) before
  committing AND before pushing**, and never merge a PR whose diff contains them.
- **Other sessions share the machine.** The scratch port and the library's `main` move under you. Use a free
  `--port`; kill a run by its pid, never `pkill -f` on a script name (it kills other sessions' runs and any monitoring commands whose command lines contain the name).
- **The app you judge through can disappear** (the user quits it). Don't start one. Score locally with
  `compile_snippet.py` (it needs the file's own typedefs: `f32` is not in its prelude) against the project's
  `asm/nonmatchings` file; a candidate that scores the same as a known-exact source is exact. Judge in the app when
  it is back.
- **One manual step is often several permuter gaps.** Here the match needed three changes at once (sink, literal, `u8`);
  each alone left the score unchanged or worse, so a one-step-at-a-time search cannot see them. The rewrite should
  emit the whole combination as one child, and its test should assert the combination, not the parts.
- **The saved function can omit the project's typedefs** (`u8`, `f32`, its structs). Fed to
  `fast_judge.py` or `compile_snippet.py` as is, it fails to parse and the score comes back `None` (looks like "no
  verdict", not an error). Build one header once (typedefs plus the externs) and prepend it to every candidate; and
  test scores with `is None`, never `score or BIG`, which turns an exact 0 into a failure.
- **`fast_judge.Judge` needs the target bytes**. If you use it on a ROM project, read the function's offset,
  size and address from the read-only project metadata and slice the ROM in place. Use the MCP judge when
  that local metadata is unavailable.
- **Check what already exists before writing a rewrite.** `passthrough_argument` already did the argument fix and
  `symbol_width_repairs` was one regex away from the cast fix; ten minutes of grep over the library's docstrings
  found both. `families.py <words>` is that grep (family, module, purpose, one line each, from the installed library); run
the existing families on the saved best first (`prove_chain.py --list`): what they don't produce is the gap.

- **Edit the best, keep an edit list, and score each row** (edit, score alone, score combined). It worked: the
  180 -> 0 table above came straight from it, and it showed which edits only help together (guard on the counter
  alone went 60 -> 193). Sharpen it: when an edit makes it worse, keep it in the list and test it again after each
  later edit, rather than dropping it; and say for each row which compiler fact it changes (addressing, a priority,
  the frame), so the rewrite you write in step 5 emits the whole set as one child.
- **zsh doesn't word-split `$VAR`**: a `for f in $FILES` or a flag string in a variable passes one argument. Wrap the
  judge in a shell function (`fj(){ ... "$@"; }`) or a small Python driver that keeps one `fast_judge.Judge` open.
- **m2c reuses one variable name for unrelated values** (a counter later holding a call's result). That ties their
  registers together; split the name before reading any register diff.

## 5c. Handing off (only by the clock, never early)

**The strongest model never hands off.** The clock exists so a weaker model (Sonnet) passes a stalled near-match
to a stronger one (Opus). An Opus agent would only hand off to another Opus, so it doesn't: it never waits for the
clock and never stops to hand off. Out of ideas, it goes back to step 1 with a new angle (another compiler profile
through a `compiler_experiments` trial, the pass that decides the rows, grids over combined edits). The one exception is its own
context running low: then it writes the note with `handoff.py` so the next agent starts from its evidence.

Persistence is the default: a near-match nobody finishes is worth more than three abandoned ones. A hand-off is allowed
only when `clock.py <holder>` (or `clock.py --slug S --function F --agent NAME --since T`) prints **"hand-off allowed"**:
at least 45 minutes on the function **and** 45 minutes since your last improvement, read from the app's attempt history,
not judged by you. Submit every new best to the app (`setup.py try <holder> CAND.c`) as soon as `fast_judge` finds it, or the clock
can't see it. Before that verdict, or while the score still improves, keep going (step 1 again: read the diff). Even
with the verdict, hand off only when you are near 0 and stuck on the last rows, or can state the stall precisely
(a compiler behaviour with no known source shape); a run that is still learning keeps working.

A near-match under about 60 that stalls is exactly what to hand off when the clock allows: the coordinator gives it
to a stronger model, which needs your evidence, not your conclusions. Don't keep it for hours past the clock.

To hand off:
1. Write the note and release in one command (it makes `add_note` and `release_function` with every argument they
   need, claim token included, then `setup.py release`):
   ```bash
   handoff.py <holder> --source BEST.c --tried "dropped the index cast: 45" --tried "cursor temp: 40" ... \
       --findings "sched2 keeps the load first; greg: pseudo 80 prefers $a1"
   ```
   The note carries the best score, the remaining differing rows, the register pairs and `cause:` lines from
   explain_regs.py, every `--tried` approach with its score (one per approach: all of them, failed ones included),
   your findings and the source's path. `--dry-run` prints it without calling anything; check it reads complete.
2. Merge any library or app work it produced (step 5), with tests, as on a match.
3. `setup.py stop <holder>`.
4. Report **"handed off"**, with the clock's line, the best score and the note's summary, so a stronger agent resumes
   from the history.

## 6. Finishing (the goal is met)

Confirm these results before reporting success.

- the app records the function as matched with a best score of 0 (`get_function(section="summary")`), and
  `judge.py diff` re-read shows "matched best 0";
- the `docs/learnings/` entry is written (step 5; skipped until reminded in one run); and
- the permuter or app change from step 5 is merged if an improvement was found, the function is
  confirmed matched in the app after the merge (`get_function(section="summary")`), and your final message gives the before/after
  evidence. Do not run `scratch_run.py`; the user tests the repair in their app.

A long search with no new idea is not progress, and not a stopping point: when the score stops moving, go back to
step 1 and read the diff again before spending more compiles.

Release any remaining claim through MCP, then `setup.py stop <holder>` (it removes the worktree, venv and scratch folder
of the session). Remove any other worktree, clone, scratch folder and private venv you made, and
never write game-derived source under `~/Documents` (scratch in `/tmp`). Then report the score, what matched the
function, every PR number, any per-function profile change, any scaffolding the match relies on, and any remaining
permuter gaps.
