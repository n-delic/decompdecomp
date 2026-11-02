# Review unanalysed ROM code

In a MIPS ROM project's **Settings → About this project**, choose **Audit unanalysed
code**. This reads a frozen image and its known segment mappings. It does not start
agents, run compilers, import candidates, or change source, targets or progress.

The report separates incoming direct transfers from weaker frame/return patterns.
Source-function buttons open the caller. Even a closed direct-flow result is only a
review lead: binary data and microcode can resemble CPU code. Calls are assumed to
return; indirect jumps, invalid instructions, outside edges and scan limits leave the
result unresolved. The decoder supports ordinary MIPS and the existing Allegrex reader;
unsupported processor-specific instructions are inconclusive, not proof of data.

Review the original bytes, loading address, caller and expected ABI. Check the return,
its delay slot, branch targets, jump tables, alignment and the following function. A
credible correction still needs a copied reimport/build and preservation of earlier
verified C. Exact acceptance remains the normal linked-byte/data or objdiff judge.

A target without a terminal return or jump now gets **Boundary review**, rather than
an automatic handwritten-assembly verdict. The warning is in the function drawer and
agent brief. Check again can reopen only the exact retired automatic fall-through
classification. Operator/library/agent decisions and real hardware-instruction assembly
are preserved. Warnings neither pause functions nor remove their bytes from progress;
claiming and deliberate work remain available. Automatic no-C votes do not convert a
boundary warning into an assembly verdict. A changed complete target clears the warning.

## Read-only exported-project audit

```
PYTHONPATH=src python -m decompnet.app.rom_gap_audit \
  --project /private/export --output /private/gap-audit.json
```

The image hash and repository-contained input paths are checked. Reports contain
addresses and evidence summaries, not ROM bytes or C. At most 128 candidates, 32
unreferenced frame hints, eight established mappings and 4,096 CFG steps per candidate
are retained. Hints can still miss frameless code, function-pointer-only entries,
compressed/overlaid code without a known mapping, and code mixed into an existing
code/data segment: this audits binary gaps, not complete game coverage.

A private N64 audit after recovering five truncated functions reviewed five remaining
gaps. It found two directly called, closed-flow candidates, one closed frame/return
hint without an incoming call, and one frame hint with unresolved flow. No new boundaries
were adopted on this evidence alone. These are leads, not four new matches.

Other processors and imported object projects need an image/segment adapter and boundary
reader (ROADMAP). They do not receive guessed MIPS diagnoses.

## Pointer and interior evidence

The audit also examines bytes inside mapped code parents that no detected function
owns. These can be data/rodata children or missed text; an interior hit is not proof
that the data classification is wrong. Aligned words outside known function bodies
are checked as possible function pointers into raw gaps or unowned interiors. A new
pointer destination must start with a frame and have closed direct flow before it
uses a candidate slot. Up to eight referring ROM offsets are retained per candidate.
Pointers are still hypotheses: tables, constants and microcode can resemble addresses.
The audit caps pointer destination probes at 512 and reports exhaustion explicitly.

The N64 importer separately recognizes a narrower recovery: an entire gap between
accepted code ranges, called directly at its start using a unique established
mapping, with a single frame, matching restores at all returns and closed bounded
control flow reaching the gap end. Pointer-only and interior leads are never
promoted by this rule. Reimport retains matching sources and private header units;
legacy lost units can be recovered from the unchanged exact attempt's header history.

## Exporting an exact entry with scattered pools

New rebuildable exports include `export_entry.py`. For an `entry_only` function the
builder recompiles C, verifies the complete linked entry and its referenced constant
data through the ordinary judge, and emits that compiler-derived entry as an object.
It retains original data in its existing image slots. Changed C that no longer matches
stops this matching-only build; it does not fall back to original instructions.

If an older export kept readonly data as unmapped raw blobs, explicit reviewed
`data_mappings` can be added to `build.json`, each with `rom_start`, `rom_end` and
`vram`. Verify these against the loader/segment layout and the actual constant bytes.
The helper rejects missing, short, out-of-image and conflicting mappings. It does not
infer a ROM/RAM delta from an address-looking constant. Normal per-function checking
and the complete image comparison are still required. Other object formats and
unverified runtime-only pools remain unsupported.

## Saved review decisions

Settings → Audit unanalysed code retains the latest report locally for this project.
Each lead has **Not reviewed**, **Needs investigation**, **Confirmed code**, and
**Dismissed** states, plus evidence/next-step notes. Confirming and dismissing require
notes. Confirmed/dismissed rows are hidden by default; use **Show confirmed and dismissed
reviews** to revisit or reset them. Saving one row preserves unsaved edits in other rows.

These are review decisions, not code adoption or function scheduling. They never pause
functions, shrink coverage, edit targets, or bypass a compiler/link comparison. Decisions
are bound to the image and examined extent. Changed ROM/layout results are marked stale
until another audit runs; changed images/extents do not inherit a dismissal. State is
local app metadata, not part of the portable C progress snapshot.

Unresolved indirect transfers now show their address and register, rather than only
"flow unresolved". A privately reviewed large function used a bounds-checked 24-entry
jump table, and all 24 table values fell within its measured body. Direct-only closure
could not prove this switch. Another balanced-frame lead appeared in repeating records
with four code-like addresses; the actual table consumer/runtime mapping still needed
confirmation. Both remained investigation leads; neither was automatically adopted.

Constant-read export errors identify the function and up to 16 distinct missing address
ranges, with byte sizes and exclusive ends. Conflicting mappings identify the precise
address too. The build still rejects unverified data; these messages do not propose or
install an inferred mapping.

## Bounded jump tables and callback consumers

The MIPS audit recognizes a tightly bounded scalar pattern: unsigned index check,
default branch, shift by two, LUI/base addition, word load and indirect jump with a
NOP delay slot. It rejects other entries into the guarded dispatch, tables above
256 entries, ambiguous mappings, unaligned/out-of-candidate targets and unresolved
case paths. At most eight tables per candidate are examined; the ordinary 4,096-step
flow budget still applies. Every distinct reachable case is traversed.

A declared code/data mapping is required to follow table edges. An established code
address delta can identify a possible raw ROM table, but its report says **mapping
hypothesis** and keeps flow unresolved. Use reviewed `data_mappings` in an exported
`build.json`, or `audit_data_mappings` in a ROM project's config, with integer
`rom_start`, `rom_end`, `vram` fields. Mappings are never installed from hypotheses.
These input changes invalidate the saved audit. `direct_flow_closed` remains distinct
from `flow_closed` using reviewed indirect edges, so importer rules do not widen.

Callback tracing connects straight-line affine address calculations, word loads and
JALR instructions. A direct cell load/call with declared mapping is stronger evidence.
Static cell evidence does not prove runtime table contents or execution. An indexed
cell remains a hypothesis: a possible index/stride does not prove it is
reachable. Known-code mapping deltas also remain hypotheses for raw data. Unknown
instructions and calls/branches (including their delay slots) invalidate register
facts. Handwritten sources are excluded. The pass is capped at 500,000 words, 512
traces and eight consumer links per candidate; incomplete scans are flagged.

A copied private project exposed a 24-entry switch automatically. With a separately
reviewed table mapping all case paths closed; without it the table stayed a mapping
hypothesis. The existing callback lead had no supported consumer trace and remained
unconfirmed. All existing targets, statuses and saved matching source were unchanged.
Closed bounded flow is analysis evidence, not termination proof, a complete call graph,
a C match or automatic permission to import a target.
