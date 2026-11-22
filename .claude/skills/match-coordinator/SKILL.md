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
   whether stalled near-matches go to a stronger model (default Opus). Don't use Haiku for matching: in testing it
   read the diff and stopped without editing, 0 of 4.
2. Find the running app (AGENTS.md, "Find the running app"). Confirm it runs current main; if not, tell the user
   scores won't agree with the agents' local judge until they restart it and run Check again. Never restart it
   yourself.
3. Start the pool: one background Agent per slot, each with the brief below.

## The matcher brief (keep it short; the skill holds the rules)

> Goal (treat like /goal; do not stop until met): match one open, unmatched function in the decompdecomp project
> "SLUG" (judged 0 by the running app) and land each permuter improvement you discover as merged PRs — the goal
> isn't met until the steps the permuter couldn't find exist as wired decomp-repair families (docs alone don't
> count). Read the latest match-function skill WITHOUT touching the primary checkout and follow it exactly:
> `git -C REPO fetch -q origin && git -C REPO show origin/main:.claude/skills/match-function/SKILL.md`, plus
> AGENTS.md from origin/main. Claim as the skill says. If the function is enormous, release it and claim another
> — once; then stick with it. Keep every file under your own /tmp/dd-<holder>/. Don't tidy a matched source —
> polishing does that. Leave no background jobs running when done. Never stop with the score above 0 to ask.
> Commits end with the model's Co-Authored-By line; PR bodies end with the Claude Code line.
> Report: function, final score as the app records it, any profile change, edit list, all PR links, scaffolding
> the match relies on, how the tools worked, and what the permuter still can't do.

Wording traps that cost runs: "claim exactly once" made agents refuse to release enormous functions — say "release
and re-claim once"; telling agents to tidy before the final try wasted time — polishing does that.

## When an agent reports

Verify before you relay anything. A report is not proof.

- `judge.py diff SLUG FUNC` must say `status matched best 0`. If not, the run isn't done.
- `gh pr view N --json state` for every app/library PR it lists: MERGED.
- Matched but no wired family → send it back (SendMessage) to build the families from its edit list, prove them
  from the saved best with prove_chain/fast_judge, never touching the saved match.
- Not matched and it stopped: if near 0 and it has worked ≥45 minutes without improving, start a stronger-model
  agent on that function with a brief that includes the full state from the report (remaining rows, edit list,
  ideas tried with scores, scaffolding, compiler findings, the confirmed compiler rules from earlier handovers).
  If it stopped early or far from 0, send it back to keep going. If it drew an enormous function and gave up, start
  a fresh matcher.
- Then start a replacement so the pool stays full.
- Agents that keep waking only to say "nothing changed" (a stale find/monitor finishing): stop them with TaskStop
  once their work is verified.
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

## Never

- Restart, update or quit the user's app, or pull/sync/branch in the checkout it runs from.
- Start the Deep Permuter or scratch runs (the user runs the permuter in the live app).
- Report an agent's result as done without checking the app's verdict and the PR states yourself.
- Let the pool run on a usage limit silently: when agents fail on a limit, tell the user and resume them (SendMessage)
  once it resets.
