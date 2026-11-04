# Code coverage and incomplete entries

Matching progress answers “how much of the detected code matches?” It does not
establish that every retail function was detected. A build can preserve raw
binary and reproduce the original game while still missing substantial code.
These checks use the original analysis image, addresses and instruction flow;
no existing game source, model calls or paid tokens are needed.

## What the app checks

For local MIPS ROM projects, an extent that can fall through its end, or a
transfer missing its delay slot, receives a derived **incomplete / boundary
review** state. This includes legacy entries saved as assembly without warning
metadata. It does not erase an operator decision, claims, attempts or matches.
The function list has a Boundary review filter; the map uses yellow and the
inspector explains the warning. An assessment from agents is labelled as an
assessment, not verified original assembly provenance.

The MIPS reader follows reachable direct branches and returning calls, allowing
closed loops and tail jumps. A return on one branch does not hide a fallthrough
on another. A final call is not a return. This is a bounded advisory check, not a
complete control-flow proof: indirect jumps, tail transfers, computed returns and
nonreturning callees can require manual analysis. Hardware assembly remains a
separate processor classification.

**Audit coverage with CPU** examines raw gaps and unowned segment interiors for
incoming calls/branches, plausible pointer destinations and bounded frame/return
patterns. It also seeds the original bytes immediately after an incomplete entry,
using that entry’s own established ROM/VRAM mapping. Candidates are review-only:
valid instructions also occur in data and microcode. It never extends a target or
imports a function automatically. Review states and evidence are saved through
the existing code-audit panel in Settings. Confirmed-code leads remain unresolved
until code is recovered into the project; dismissals cannot clear incomplete
existing entries.

## Progress and completion

The overview reports:

- Detected-code coverage, explicitly separate from whole-image recovery.
- Current incomplete entries, including their saved status and byte extent.
- Raw-gap and unclassified segment-interior byte totals from a current audit.
  These are review-region bytes, not an estimate of undiscovered code.
- Unresolved candidates, stale audits and search limits.

When the detected queue becomes empty, the overview automatically starts the
bounded CPU audit once per project-layout revision in that page. Progress and
errors remain visible. Failures require an explicit retry, rather than an endless
polling retry loop. The API reports `completion_audit_required` until the audit
has run; headless callers can use the existing code-audit job endpoint.

Layout/ROM mapping changes invalidate the saved audit. An empty bounded audit
only means no pending findings in that search: it is never proof of complete
retail code discovery or “100% decompiled.” The interface retains that caveat even
when every detected C candidate matches. File hash mismatches reject the audit.

## Reviewing a candidate

1. Inspect adjacent original bytes, incoming edges and the established mapping.
2. Determine whether it is a complete function, an internal label, data,
   microcode, or an unresolved boundary. Follow every reachable path; do not
   assume an archived size or the next detected address is its end.
3. Record the evidence in Settings → About this project → Audit unanalysed code.
4. Recover the reviewed extent through the normal importer/reimport or proven
   piece-joining workflow, preserving original bytes and history. A review alone
   does not modify the build.
5. Rejudge recovered source normally and verify the complete build. Rerun the
   coverage audit after layout changes.

## Supported formats and limits

Boundary flow and raw-region audit adapters currently cover N64, PS1, PS2 scalar
MIPS and PSP local ROM projects, in each console’s byte order. Imported object
projects and other processors still need adapters; their absence is explicit in
ROADMAP. No console-name decisions are added to shared code.

The audit keeps its existing candidate, mapping, pointer and flow bounds. Hitting
a bound is visible and cannot be treated as a clean completion audit. A partial
or compressed image does not reveal code that has not been unpacked/mapped; a
whole-image coverage claim needs a separate validated code/data inventory.
