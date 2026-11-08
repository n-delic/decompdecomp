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

## Recovering a reviewed raw tail

Each incomplete overview entry has **Review extension**. Enter the exclusive hex
end address and the evidence for it. The local operator API also accepts a batch
at `POST /api/recover-code`: `changes` is a list of `{function, end, reason}`.
No source is needed to review control flow, but original source/objects can be
additional evidence when available. Never infer a boundary from source size alone.

Recovery preserves the original prefix and reads all new bytes from the verified
analysis image. It rejects matched or claimed entries, overlaps, mixed mappings,
and boundaries crossing a different code/data parent. A private Splat run must
retain exactly the reviewed extent before anything is published. Input rows,
settings and claims are checked again after that run. The entry reopens, prior
attempts and review evidence remain, and the changed layout is saved through the
normal progress workflow. Rebuildable export moves the consumed raw-tail boundary
so those bytes cannot be included both as code and as an incbin.

An independent leaf hint now also considers small, complete, ABI-compatible
intervals bounded by known function bodies, allowing up to 12 bytes of zero
alignment. It remains weaker review-only evidence, not an automatic function.

## Measuring missed-code detection

`python -m decompnet.app.coverage_benchmark --image <owned-demo-ROM> --elf
<matching-debug-ELF> --output <report.json>` runs controlled corruptions. The ELF
text must match the ROM mapping exactly. Ground-truth symbols construct and score
cases; the audit receives only original bytes and the damaged detection layout.

On 4 November 2026, a deterministic size-spread sample of 24 among 96 functions
in the owned N64 demo produced 72 cases:

| Corruption | Before the bounded-leaf hint | After |
| --- | ---: | ---: |
| Entire function hidden | 22 / 24 detected | 24 / 24 detected |
| Only a 4-byte opening retained | 24 / 24 detected | 24 / 24 detected |
| Opening incorrectly classified as assembly | 24 / 24 detected | 24 / 24 detected |

The final sample had zero candidate starts outside the ground-truth function
bodies and no search limits. This is one small demo sample, not an estimate of
retail recall or false-positive rate. Detection also does not prove the proposed
end address, semantic identity, source language or a C match. Raw data and microcode
remain possible false positives. Keep reporting bounds and unresolved regions.

The low-level audit accepts disjoint `review_ranges` to examine every raw block
individually when a global candidate cap fills. Such results are explicitly
partial; a partial scan cannot replace the saved whole-project completion audit.

## Repairing detected extents

Settings → Code coverage → **Review & repair boundaries**, or the same action in
an incomplete function's inspector, audits the frozen targets without changing them.
Incomplete MIPS control flow and singleton nops wait outside automatic agent, draft,
autopilot and permutation queues. A small returning function with its delay slot is
not suspicious just because it is small. Existing claims are not cancelled.

Select an entry and preview one decision: include a missing delay slot, join a
preceding piece, adjust the exclusive end address, classify padding/data, or keep an
intentional separate entry. Suggestions are leads; they are not automatic proof.
The preview shows original surrounding assembly, old/new sizes and every retained
entry affected. Record the evidence before **Verify & save decision**.

Repairs currently require one unambiguous mapped MIPS ROM segment (N64, PS1, scalar
PS2 and PSP). Imported object projects and other processors need repair adapters;
there is no generic byte-size heuristic for them. Leave a shared room before changing
layout. Release affected claims first. Matched entries cannot be resized or absorbed,
and all existing matches must pass the normal judge before a decision is committed.
A changed image, mapping, header, source or preview is rejected.

Absorbed pieces remain archived with their original IDs, sources, attempts and notes.
Resized entries retain C history but lose stale scores and need a fresh byte match.
Shortening an entry retains a separately reviewable tail. Padding/data are excluded
from detected-code totals and reported separately; classification is never a match
or a whole-image completion claim. **Keep as separate entry** explicitly permits
agent work while retaining the warning and decision evidence.

**Repair history → Verify & undo** restores the prior rows and symbol size. Undo
refuses active claims, new candidate attempts, changed inputs or newly matched
entries, so it cannot silently replace later work. No original ROM bytes are edited.


## Guided boundary decisions and agent advice

Open **Review & repair boundaries**, then select an entry. The review loads original-image
instructions around its start and end, adjacent entries, direct calls/branches and mapped
symbols before asking for a decision. Large functions use separate instruction windows so
the disputed end is visible. Missing sections are labelled. All end addresses are exclusive.

Choose a delay-slot extension, a join supported by control flow, an intentional separate
entry, padding or data. Manual end-address editing is under **Advanced boundary edit**.
Nothing is preselected: adjacency alone does not establish shared ownership, and a single
nop is insufficient proof of padding. Preview shows which name remains, archived entries,
and any retained shortened tail. Resized functions require a fresh ordinary match.

**Ask an agent to investigate** uses a configured provider/model and normal session
accounting. It has a persisted ten-minute deadline, includes provider waits, and stops
when a structured recommendation is submitted. Its tools can read original boundary
evidence and save advice; they cannot claim, compile or edit a function. A busy function
can be investigated without stealing its claim. Existing claim guards still apply when
the user previews or applies a repair. Closing the review leaves the investigation in
the Agents tab; **Refresh recommendations** retrieves saved advice when returning.

A recommendation includes its action and optional exclusive end, evidence strength
(not a probability), supporting and counter-evidence, remaining uncertainty, provider/model
attribution and links to supplied instruction addresses. **No clear recommendation** is
valid. Reports are tied to the original image and project revision; changed inputs mark
old advice stale. **Review this proposal** selects a choice and copies its explanation,
but does not preview or save the change automatically.

The direct-edge scan reads up to 65,536 mapped original instructions in the same segment,
prioritising the selected entry and nearby entries, and retains at most 80 relevant edges.
Indirect references, raw gaps and other segments/overlays remain unresolved. Symbols can
be generated rather than debug evidence. These limits are shown to both user and agent;
absent callers, agreement between models or successful safety checks cannot prove ownership.

**Run safety checks & save decision** retains the original mapping, overlap, active-claim,
revision/image and existing-match checks, evidence requirement, history and guarded undo.
These checks protect existing work; they do not prove the new boundary is correct.

Guided repair and advisory investigation currently use mapped MIPS ROM extents (N64,
PS1, PS2 scalar MIPS and PSP). Other processors and imported builds need evidence/layout
adapters before this workflow can safely reach them; they remain planned in ROADMAP.md.

## Automatic import repairs

New MIPS ROM imports and explicit reimports repair generated fragments before creating
project targets. The pass accepts only a bounded, closed, single-frame scalar body
with balanced return delay slots in one unambiguous mapping. It can join fragments,
deduplicate an overlapping delay slot, and separate a following framed routine. Every
original instruction is retained; this is boundary analysis, never a C match.

Explicit symbols, data, independent calls/branches, recorded function pointers,
competing overlays, invalid bytes, switches and unresolved flow prevent automatic
ownership changes. New interior frame entries require a preceding balanced return.
N64 segment detection also completes a parent cut after just the first one or two
prologue instructions, through its matching return and stack pop, without crossing
another parent. That segment rule does not establish indirect or absolute-jump ownership.

Existing projects use **Reimport** to receive the new analysis and the ordinary saved
work restoration/judging protections. Opening a project does not rewrite its targets,
claims or matches, and shared rooms still block reimport. Ambiguous mappings, kernel
stubs and unproven entries retain the review workflow. Non-MIPS processors and project
mode builds need their own boundary adapters; they receive no guessed MIPS repairs.
