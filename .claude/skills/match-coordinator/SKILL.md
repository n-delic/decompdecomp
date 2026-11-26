---
name: match-coordinator
description: Run a long matching session as a coordinator: keep a pool of subagents matching functions in a decompdecomp project with the match-function skill, verify every result, replace finished agents, hand stalled near-matches to a stronger model, and fold what the agents learn back into the skill and tools. Use for "keep N agents matching PROJECT", "run matching overnight", or "manage the matching agents".
---

# Coordinate a pool of matching agents

You are the coordinator. You don't match functions yourself; you keep agents working, check what they claim, and
turn their friction into better tools. Every agent follows the `match-function` skill; your job is everything
around it.

## Setup

1. Ask (or take from the request): project slug, pool size (default 4), model for matchers (default Sonnet), and
   whether stalled near-matches go to a stronger model (default Opus). Haiku gets
   `match-function/HAIKU.md` as its brief, one function per agent run. Use Opus for
   stuck near-misses and large functions (over 100 instructions), Sonnet for builders and sweeps.
   Pool sizes that worked: about 4 Opus + 4 Haiku + 2 builders at once. That is the machine limit; each agent runs one
   judge process at a time (parallel grids overloaded it twice), so tell them so.
2. Find the running app (AGENTS.md, "Find the running app"). Confirm it runs current main; if not, tell the user
   scores won't agree with the agents' local judge until they restart it and run Check again. Never restart it
   yourself.
3. Start the pool: one background Agent per slot, each with the brief below.

## The matcher brief (keep it short; the skill holds the rules)

> Goal (treat like /goal; do not stop until met): match one open, unmatched function in the decompdecomp project
> "SLUG" (judged 0 by the running app). Sonnet/Opus matchers: also land each permuter improvement you discover as a
> merged PR; the goal isn't met until the steps the permuter couldn't find exist as decomp-repair families that declare
> `REPAIR` (the app needs no wiring; docs alone don't count). Haiku matchers: report repairs, don't change the repo or
> open PRs (the repair report goes in `report_repair` and your final report: symptom, C before/after, profile). Read the latest match-function skill WITHOUT touching the primary checkout and follow it exactly:
> `git -C REPO fetch -q origin && git -C REPO show origin/main:.claude/skills/match-function/SKILL.md`, plus
> AGENTS.md from origin/main. Claim as the skill says. If the function is enormous, release it and claim another
> — once; then stick with it. Keep every file under your own /tmp/dd-<holder>/. Don't tidy a matched source —
> polishing does that. Leave no background jobs running when done. Never stop with the score above 0 to ask.
> Commits end with the model's Co-Authored-By line; PR bodies end with the Claude Code line.
> Report: function, final score as the app records it, any profile change, edit list, all PR links, scaffolding
> the match relies on, how the tools worked, and what the permuter still can't do.

**Brief Haiku with `.claude/skills/match-function/HAIKU.md`** (read from origin/main), one function per agent run:
give it the project slug, the primary checkout's path and, if you have one, the function name; start a fresh agent for
the next function.

Brief template notes (for a Haiku or any list-based agent):
- List the functions by name with each saved best, instruction count and any hint from earlier notes.
- Say who else holds which functions (claims keep agents apart) and give each agent its holder name.
- Paste the profile findings: which address stretches use which compiler.
- Only Haiku agents are barred from repo changes and PRs (tell them: "report repairs, don't change the repo"). Sonnet
  and Opus matchers land the repair PR themselves (tell them so, and to check decomp-repair main first, sync before
  release, add tests and a learnings file; the library-pin PR is opened by the workflow). Don't attribute rules to the
  user that the user did not state (no "as you asked" for a rule you invented); the user's preferences are: merge PRs
  right after creating them, and never restart or update their running app.

Wording traps that cost runs: "claim exactly once" made agents refuse to release enormous functions — say "release
and re-claim once"; telling agents to tidy before the final try wasted time — polishing does that.

## When an agent reports

Verify before you relay anything. A report is not proof.

- `judge.py diff SLUG FUNC` must say `status matched best 0`. If not, the run isn't done. Also read the app's database
  read-only (`sqlite3 "file:<home>/.decomp/decomp.db?mode=ro"`): `status matched` and best 0 per function. Agent
  reports sometimes overcount, and other interactive sessions (`manual-...`) work the same lists, so many functions
  turn out already matched.
- `gh pr view N --json state` for every app/library PR it lists: MERGED.
- A function is not done until its permuter improvement exists as a merged family, for Sonnet/Opus matchers. For Haiku
  matchers collect their repair reports and, after each batch, start a Sonnet builder for every repair no existing
  family covers (check decomp-repair main's registry first). Matched but no declared family (non-Haiku) → send it back (SendMessage) to build the families from its edit list, prove them
  from the saved best with prove_chain/fast_judge, never touching the saved match.
- Not matched and it stopped: if near 0 and it has worked ≥45 minutes without improving, start a stronger-model
  agent on that function with a brief that includes the full state from the report (remaining rows, edit list,
  ideas tried with scores, scaffolding, compiler findings, the confirmed compiler rules from earlier handovers).
  If it stopped early or far from 0, send it back to keep going. If it drew an enormous function and gave up, start
  a fresh matcher.
- Then start a replacement so the pool stays full.
- Agents that keep waking only to say "nothing changed" (a stale find/monitor finishing): stop them with `TaskStop`
  once their work is verified; stale monitor timers keep waking you otherwise.
- Watch machine load (`uptime`). If agents overlap grids, tell them to run one process at a time.
- An exact compiler trial left queued with `review_only`: have an agent re-apply it with `suggest(function, trial_id)`
  without it.
- Boundary problems and SDK-versus-assembly classification are the user's calls: collect them in your report.
- Clean up anything an agent left: game-derived files outside /tmp, scratch folders it named, stray claims.

Tell the user, per agent: function, start → final score, what matched it (the decisive edits, briefly), the new
rewrites and whether they reproduce the whole path, scaffolding, what the permuter still can't do, PR links, and
any problem it caused. Keep it short; no internal IDs.

## Between reports

- CI and merge-conflict events for the agents' PRs: resolve them in a detached worktree outside the primary
  checkout (merge origin/main, keep both sides' intent, check library pins contain each other's modules, grep for
  conflict markers, run the related tests). If the owning agent already pushed, discard yours.
- Watch for patterns across reports — repeated tool friction, the same missing rewrite, the same wrong turn — and
  turn each into a fix: a skill rule, a script, a library family, or an app change, through its own agent and PR.
  Recurring examples that paid off: per-agent folders and claim renewal (setup.py), fast_judge --diff, passes.py,
  prove_chain.py, families.py, a release workflow instead of agents tagging, per-entry learnings files, the shared
  compiler-source folder.
- Every few completions, review agents' transcripts (through a subagent; never read a transcript file yourself) for
  lessons to add to the match-function skill.
- When a stronger-model handover matches, collect the compiler rules it confirmed in the source; they make the next
  handover faster.

## Turn findings into library families

After each batch, read the agents' repair reports (Haiku) and "what a permuter would need" lines. For each repair no existing family covers, and each recurring rewrite, start a Sonnet builder
(its own worktree and PR in decomp-repair; check decomp-repair's main first to avoid duplicates). Built this way in one
session: `float_argument_prototype`, `induction_reverse_reduction`, `void_returned_local` (extended), `float_literal_global`
(extended), `merge_disjoint_temporaries`, `small_data_global_local`, `second_field_read`, `callback_pointer_local`,
`constant_minuend_split`, `adjacent_store_swap`, `cursor_load_modify_store`, `global_array_cast_form`,
`pointer_walk_to_index`, `index_to_pointer_walk`, `stored_field_readback_index`; and the toolchain pack
`sn-n64-272-0001-as0004`.

## Never

- Restart, update or quit the user's app, or pull/sync/branch in the checkout it runs from.
- Start the Deep Permuter or scratch runs (the user runs the permuter in the live app).
- Report an agent's result as done without checking the app's verdict and the PR states yourself.
- Let the pool run on a usage limit silently: when agents fail on a limit, tell the user and resume them (SendMessage)
  once it resets.
