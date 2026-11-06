# Repair evidence sampling

New successful captures are sampled by source transformation and recorded compiler context.
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
remains available for review and replay. This change does not create a submission service
or a separate evidence database; portable exports still need to resolve referenced inputs.

## Settings

Settings → Database storage → Repair evidence controls future captures across projects.
Defaults remain one automatic example and three AI/manual examples per pattern. Each
limit accepts 0–1000; zero retains counts only for comparable patterns. Turning off
“Limit repeated repair examples” retains every new example, subject to the existing
identical-recheck suppression. Incomplete evidence still bypasses sampling. Changing
limits does not delete historical examples or reset counters; raising a limit allows
more examples to be retained until the new limit is reached.
