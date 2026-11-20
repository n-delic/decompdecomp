# Repair evidence sampling

Deep permuter and Autopilot wins are credited as matches and keep their attempt history.
They do not enter Repair examples or queue automatic repair replays. Retained historical
examples remain available for review.

Other successful captures are sampled by source transformation and recorded compiler context.
An installed automatic rule retains one regression example per bucket. Manual/AI repairs
retain up to three representative examples. Further successes increment durable counters
in `repair_capture_groups` without storing another full example or scheduling another
post-match analysis. Matches, attempt history and lifetime metrics are unchanged.

Buckets normalize identifier names in changed C tokens, preserve numeric/string literals,
and include platform, compiler/flags, header hash, scoring revision, automatic family and
multi-rule path when recorded. They are sampling heuristics, not proof of semantic
identity or a mechanism that enables repair rules. Different headers/compilers remain
separate. Incomplete baselines, incomparable settings, unchanged source, imported builds
without complete replay inputs and large sources are retained conservatively.

Existing evidence and manual review notes are preserved; there is no startup scan or
retroactive deletion. Sampling counters start with this update. Existing large-text
compression and explicit compaction continue to work. The original retained example
remains available for review and replay. Full evidence now resides in the separate database described in
[repair-evidence-database.md](repair-evidence-database.md); no submission service is enabled.

## Settings

Settings → Database storage → Repair evidence controls future captures across projects.
Defaults remain one automatic example and three AI/manual examples per pattern. Each
limit accepts 0–1000; zero retains counts only for comparable patterns. Turning off
“Limit repeated repair examples” retains every new example, subject to the existing
identical-recheck suppression. Incomplete evidence still bypasses sampling. Changing
limits does not delete historical examples or reset counters; raising a limit allows
more examples to be retained until the new limit is reached.

## Classifying new matches

Agents can include a single category on a candidate submission. The app saves it on the
verified repair example. If a repair matches after a failed attempt and the category is
missing, the matching tool asks the agent to classify the captured example before claiming
more work in the current app session. A category alone is sufficient. Under `other`, agents can suggest a short new category
for review in **Agents → Repair examples**. Suggested categories are included in exports.

Agents can search the current project's captured repairs by category. The search ranks
up to 256 recent examples and returns up to 12 one-line summaries. A single live claim
supplies context automatically. An explicit function also works for batches. The search
uses recorded compiler settings and short samples of target/candidate instruction
mismatches. Older captures can use target instruction overlap when their failed diff
wasn't recorded. Agents can open up to six selected IDs in one call for bounded diff
excerpts and compiler settings. Votes are advisory, persisted once per agent and example,
and break relevance ties. The tools use saved evidence and run no compiles.

In **Agents → Repair examples**, each card shows its upvote count. Choose
**Most upvoted** to rank the filtered captures by vote count, with newer captures first
when counts tie. Groups of equivalent captures show their most upvoted example in this view.

**No saved failed baseline** holds exact-match captures without a saved, compiled
candidate that scored less than an exact match. A first judged build can match, and
drafts, automatic reuse and reconciliation of saved matches can reach an exact result
without a recorded near miss. Earlier compiler errors can be in the history without
providing a scored baseline. Use the category to browse those captures separately from
**Other** source repairs. Existing review states, notes and votes are retained when
older captures are moved to this category.

**Memory access order** covers edits that change the sequence or number of loads or
stores, including volatile accesses. Use **Addresses** for changes to how addresses
are formed and **Expression order** for arithmetic evaluation order.
