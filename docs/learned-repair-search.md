# Learned repair search

The app records short repair sequences when a connected chain of tested candidates ends in an
ordinary exact judgment. It stores family names, compiler/build profile, initial mismatch kind
and success count, not game source. Chains of two to four steps can rank applicable families
in future searches. Compiler installation identity is included for ROM profiles; imported
projects stay isolated by their own build scope.

Shared family ordering promotes learned first steps while retaining the original exploration
queue. Deep local search can follow the rest of a learned sequence from its actual tested
parent. It regenerates candidates and retargets families using that parent's own diff after
each improvement or retained detour. A losing trial's diff is not the saved winner's evidence.
Recipes never manufacture a verdict, skip verification, or make unsupported families available.
Random rewrites outside the tracked local chain do not get credited as recipe steps.

## Project sweep time

Minutes per function is a maximum. Project sweeps start with half that time as a soft allowance.
Each measured improvement extends the soft deadline by half the configured time from that
improvement, up to the original hard deadline. After at least 15 seconds, a window of 32 fresh
scored trials containing at most six distinct function outputs can rotate early when no
improvement intervenes. Improvements clear that window. Baselines, cached misses, no-edit
proposals and failed compiles do not train the repeated-output window.

A rotated function stays open. It is visited at most once in that sweep and is eligible on the
next requested sweep, without editing its source or resetting passes. No function is paused.
Time/compile caps and cancellation remain authoritative. Explicit single-function runs retain
their full requested time; adaptive rotation applies only to project sweeps.

## Agent evidence and regression alerts

Function briefs can include up to four remaining assembly-difference rows, current-input local
trial counts, learned starting families and two recent near-score alternative summaries.
These notes are bounded and tied to build inputs. Full diff and history remain available on
request. Displayed assembly and similarity scores are advisory, not exact verification.

A failed recheck of the source captured in a local last-exact record raises a notification and
marks that record with the latest failure time/score. Different trial source, stale compiler
results and imported historical records cannot raise this alarm. Repeated failures update the
record without flooding notifications; a new exact judgment clears it. Input changes are
explained by the existing verification comparison. If the tracked inputs agree, the alert
still reports the failure rather than guessing a cause. The alarm itself does not alter code,
flags or function status.

## Controlled check

An isolated KMC scheduling checkpoint recovered 945 → 155 → exact using call initialization
and masked-value reuse. The first integrated run used nine compiles including baseline;
resetting the checkpoint with the learned recipe and cleared miss cache used five. Both took
about eight seconds on this host. This is a retrospective regression control, not additional
game progress, a fresh-game benchmark, or evidence of measured agent-token savings.
