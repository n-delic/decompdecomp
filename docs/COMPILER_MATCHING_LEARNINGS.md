# Compiler matching learning log

Keep this file updated when a verified match explains a reusable compiler behaviour. Agent-facing
summaries live in `src/decompnet/consoles/*/guides/`; this is the evidence ledger, including failed ideas.
Do not treat an instruction similarity score as proof of a match.

## How to add a finding

Record the compiler **version and flags**, processor/ABI, original mismatch, source change, measured
result, evidence location, and remaining uncertainty. Distinguish a controlled same-input experiment
from a sequence of edits. Keep original line layout, headers, symbol addresses and target bytes fixed
unless that item is the variable being tested. Do not commit game data or compiler binaries here.

Use these evidence levels:

- **Verified case:** the normal judge accepted exact linked bytes (or objdiff 100% for an object project).
  A ROM publication also needs the complete image rebuild. One case is not a universal compiler rule.
- **Controlled regression:** undo one aspect of a known solution and check that a generated repair
  recovers it. This tests the generator; it is not an additional newly matched function.
- **Observation:** output changed, but the mechanism or all contributing edits are not isolated.
- **Hypothesis:** worth trying, with no successful controlled test yet.

## KMC GCC 2.7.2, 32-bit big-endian MIPS

### Verified case: a large rendering/menu function

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Evidence: [published matching source and verification notes](https://github.com/RetroReversing/v-rally-decomp/commit/31c03a37ad31e2f106df7df51c5380307ad08056).
Function `func_8006090C`: 883 instructions / 3,532 bytes. The normal app judge and standalone check
both accepted it; the 12,582,912-byte cartridge rebuilt identically, SHA1
`da3f262dfb86cff1376c9a1aa081212e5a7aad64`.

| Source change | What changed in the generated code | Evidence and limits |
| --- | --- | --- |
| Represent a 3-byte string copy with `__builtin_memcpy` | Recovered signed halfword/byte loads, store order and a cached destination address | Contributed to the final match. The source was a real 4-byte char array with `aligned(2)`; dropping alignment produced byte loads and worsened the score. Do not copy 3 bytes from a declared 2-byte scalar or assume adjacent locals form a buffer. |
| Recover record, actor, packet and graphics-command types | Recovered field addressing, alias information, load order and register allocation | Real sizes/offsets matter, including nested record tables. Candidate-local types were used; no shared layout was guessed globally. |
| Remove an artificial outer `do { ... } while (0)` | Recovered initial lookup and first-loop registers | Score 1425 → 1055 in that experiment. Scope/loop structure matters; do not remove a wrapper containing loop control blindly. |
| Merge the later UI pointer lifetime and time-accumulator lifetime together | Restored the intended saved registers | Score 1055 → 1010. One of these edits alone made allocation worse. Textual non-overlap is not sufficient proof of liveness across loops. |
| Use actual globals instead of invented offsets from a common base | Changed common-subexpression sharing and load scheduling | Several staged improvements; not all gains were isolated. An address name is not a substitute for the object's actual declaration. |
| Compute a positive field address, then derive the matrix base with a negative element offset | Recovered the shared base register and zero store | A score-480 alternative contained the desired addressing where the score-440 candidate did not. Reordering setup on the alternative led to score 210. Preserve promising alternatives even when total score regresses. |
| Use a small lexical block and local graphics-command pointer, with the actor read at the call | Recovered the final constant-load placement | Score 210 → 0. The block and actor-load change were coupled. This does not show that braces alone change every GCC build. |

### What did not help this case

- 300 declaration permutations were neutral; broad register qualifiers and local retyping mostly failed.
- Broad optimization, CPU and debug-flag comparisons did not beat the original pinned profile.
- 600 region permuter candidates did not improve the then-best score of 440.
- A five-minute earlier deep pass tested 325 candidates plus two cached misses with no improvement.
- Many matrix/union/volatile/zero-copy alternatives failed. Avoid repeating these families indefinitely
  on unchanged input; retain scoped diagnostics and cache outcomes with source/header/compiler keys.

### Automation derived from this case

`app/compiler_shape_repairs.py` proposes bounded short copies, existing typed field accesses, one-shot
loop/scope alternatives, pointer block scopes and shared array addresses. `app/draft_repairs.py` also
bundles two conservative scalar lifetime merges. These run through the existing draft/permuter paths,
not an AI API. They do not reconstruct arbitrary missing record layouts or prove source equivalence.

`app/iterative_repairs.py` reserves one beam slot for a candidate up to 25% worse than the branch's
best score. The ceiling cannot compound at successive depths. Phase budget, depth limits, output
fingerprint limits, cancellation and fair function rotation still apply. These alternatives live in
search state, not in the saved best source. Only the normal judge accepts a match.

Controlled private regressions of the published function exercise the generated repairs. These are
regression checks, not claims of extra game progress:

- Undoing both 3-byte copies scored 2675. The combined short-copy candidate recovered exact bytes;
  the individual-site candidates scored 1335 and 1580.
- Replacing typed actor accesses with raw offsets scored 210. The typed-record candidate recovered
  exact bytes.
- Isolating matrix-address and pointer-scope edits in the final solution did not lose the match;
  generated candidates stayed exact. These checks establish compatibility, not a measured improvement.

Single-function deep search also explores up to three bounded retained alternatives per local-repair burst under its
existing deadline/compile allowance. It returns the saved best source when the detour fails; only the
normal judge can save a later improvement. Persistent multi-function iterative state is separate.

### Verified follow-up: a 966-instruction menu function

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Evidence: [published exact checkpoint](https://github.com/RetroReversing/v-rally-decomp/commit/4562473).
The normal app judge, independent standalone function check and complete 12,582,912-byte cartridge
comparison accepted the result. All previously selected C remained selected.

**Controlled final repair:** materializing a `u16` mask into an existing, subsequently reset 32-bit local
recovered the target's in-place saved-register mask. A fresh temporary or assignment back to the input
did not produce the same allocation. Reusing a non-overlapping local is a hypothesis to compile, not
proof of the original source's variable names or grouping. The final source also retains earlier
reconstruction choices; matching bytes do not establish portable source semantics or original spelling.

Other observations from the staged search:

- Associating a table displacement with its base before adding the index recovered address ordering.
- A small bitfield local in an inline helper changed frame reservation. Ordinary unused array padding
  shifted other locals and failed. This does not establish a general-purpose stack-padding idiom.
- A redundant conditional disappeared from the instructions but changed scheduling/allocation. Moving
  loop-counter initialization into an existing comma-expression recovered a call-delay-slot placement.
  These edits were coupled; do not attribute the entire gain to one predicate or one line break.
- Broad flag/type/declaration experiments were mostly neutral or worse. A controlled Linux-host versus
  macOS-host compiler comparison emitted identical text for the tested input. No host-specific fix was
  needed. Preserve the pinned profile, including `-g3`.
- The final private disassembly score remained 120 because 12 numeric-versus-symbolic relocation rows
  rendered differently. The linked-byte verdict was exact; the app correctly recorded score zero.
  Never use a heuristic score, normalized display or register similarity as the acceptance gate.

**Automation:** `masked_reuse_repairs.py` materializes a cast/mask into a local proven dead by the bounded
CFG in `local_flow.py`. Its graph includes if/else, loop back edges, switch fallthrough, break/continue
and direct gotos. Unknown syntax, escaped/qualified locals and unsupported control flow give no advice.
The same analysis supplies additional lifetime merge candidates and small m2c draft hints without
rewriting the default draft. These candidates share existing budgets and the unchanged judge.

Search classification resolves known MIPS relocation operands before choosing weights, without changing
the original rows or score. Deep/random search retains a bounded numeric/structural candidate frontier;
iterative repair retains the numeric winner alongside structural alternatives and its existing bounded
detour. New random improvements immediately seed a new batch; newly saved improvements are remeasured
with current headers/settings. Existing scoped compile caches remain in use.

**Private checkpoint replay:** the new masked-reuse family recovered exact linked bytes on its first
candidate from both the score-130 and score-155 checkpoints, in approximately 1.1–1.2 seconds including
a baseline compile on the test machine. The integrated deep local-repair phase also saved exact
matches from both checkpoints: two compiles including baseline, approximately 3.2–3.4 seconds
including proposal generation and bookkeeping. It did not finish the earlier score-180 checkpoint within its
24 generated candidates. These are retrospective local-family checks with an already selected compiler
profile, not fresh-start timing, extra progress or proof that the original 4h25m investigation now takes
one second. Game source/ROMs and private checkpoints are not included in the app repository; regression
fixtures here are synthetic.

## IDO 5.3 / 7.1, 32-bit big-endian MIPS

### Observation: source layout sensitivity (IDO 5.3)

The existing [IDO guide](../src/decompnet/consoles/n64/guides/ido.md) records a private same-source
comparison: preserved whitespace scored 0; replacing every whitespace run with a space scored 120
and changed call/load ordering. This establishes layout sensitivity for that input. It does **not**
isolate newlines alone or establish instruction scheduling as the mechanism.

A broader source-assisted pass gained 22 matches, but header/global declaration repairs also changed.
Do not attribute all 22 to line breaks. Preserve successful formatting; compare preprocessed tokens,
compiler/flags, headers, macros and linked instruction bytes before making stronger claims.

### Existing guide advice, not new controlled findings

Float/double literals, argument home slots, expression shape, global reloads and first assignments can
matter. IDO 5.3 and 7.1 are separate profiles; no result here establishes that a 5.3 observation applies
to 7.1. Record future tests under their exact version and flags.

## Other GCC families: SN64, EGCS and GNU GCC

These share some source idioms with KMC but use different compiler/assembler versions and profiles.
No new controlled successful case for these versions was established in this investigation. Do not
transfer the KMC score improvements or whitespace effects to them as measured facts. Record each
profile independently, including debug/assembler options and endianness.

## CodeWarrior, MSVC, ARM compilers and other installed profiles

No new per-compiler match evidence was collected in this investigation. Existing console guides remain
available; the architecture-neutral source hypotheses may be tested by a supported project's normal
judge. Unsupported builtins or incompatible types should fail compilation, not trigger a profile change.
Add a versioned subsection when a successful, reproducible case is available. Do not fill this ledger
with inferred compiler facts simply because a profile is selectable.

## Rules shared by every compiler

1. Preserve source plus context, flags, symbol map and verification evidence together.
2. Match machine bytes, not a percentage rounded to 100 or a disassembly with cosmetic relocation labels.
3. Local declarations, object bounds and alignment are part of the experiment. A cast cannot establish
   an object's size, and arbitrary padding/NOP tails must not inflate progress measurements.
4. Keep promising structural alternatives, bounded by explicit CPU budgets. A local gain can accompany
   a global register-allocation regression.
5. Log neutral and failed families too. Revisit them when their inputs or the repair rules change.
6. Rejudge after formatting or polishing. Preserve prior verified work and never weaken the target/judge.


## IDO 5.3: typed SDK context, structure returns and export layout

**Verified cases (32-bit big-endian MIPS, source assisted):** a recent six-match continuation used
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`, retaining each function's selected `-O2` or `-O3`
profile. Native structure return replaced an explicit output pointer plus final record copy, recovering
the compiler's hidden result-pointer ABI. Typed auxiliary-bus layout, original command macros, argument
order, and inline word setters' pointer locals recovered other functions. A separate candidate needed
an unresolved global's ROM-derived address. The complete standalone cartridge was verified; these
observations do not identify the original IDO release uniquely or isolate every individual source edit.

**Controlled regression:** `tests/test_structure_return_repairs.py` compiles a homemade record-return
example with already-installed IDO 5.3 at `-O3 -mips2 -G 0 -non_shared -Xcpluscomm`; the generated native
return has the target object's exact normalized function fingerprint. This tests the repair generator,
not an additional game recovery. Typed inline-setter proposals have bounded source tests and still need
the destination's ordinary byte judge. No shared record layout is invented.

**Failed/neutral observations:** restoring the SDK bodies and line layout of two tiny sequence byte
readers still left temporary-register differences under `-O2`. Do not repeat that as an assumed fix.
A four-instruction thread-list helper also retained a temporary-register difference after restoring
its pointer return. Neither case was counted as a match.

**Integration evidence:** a function-level exact match can contain a string and numeric/jump-table
constants that need different bases within one object section. The case required referencing the
original string separately before the whole build matched. `tests/test_candidate_diagnostics.py`
checks non-contiguous placements and extra bodies without changing the function's exact verdict;
`tests/test_repository_sync.py` protects both expanded-image and original-cartridge hashes.
A successful local layout check is not proof of a full rebuild.
## Follow-up: adaptive priorities, region selection and inferred records

Profile-specific outcome learning now records fresh compiler trials by compiler/flags/ABI and mismatch
kind. It waits for 32 observations per family, smooths sparse results and clamps multipliers to
0.25–4. Random search retains alternating upstream-default batches; deterministic queues interleave
learned and original orders. Cached deep misses do not train twice. Old aggregate counts are not
silently assigned to current compiler settings: their historical profile/focus snapshots are absent.
Imported builds are isolated by their actual build-evidence scope rather than a guessed compiler.
This is a search policy, not evidence of a measured speedup yet.

Target-aligned 16-instruction regions now help choose the reserved worse-score branch. The original
883-instruction case was replayed with fixed compiler/context/target: the score-480 alternative
recovered three regional mismatches relative to the score-440 candidate. That is a useful partial
recovery, not an exact match; the previously verified final source still scores 0. Target stream
identity is required, so inserted candidate instructions cannot shift the comparison.

Candidate-local record inference now recognises multiple explicit byte-offset accesses, including
m2c's parenthesised forms. It requires at least three accesses to at least two consistent,
non-overlapping, aligned fields, with bounded offsets and no volatile/unknown types. The inferred
record is private to a source candidate. It does not assert the full object size, edit a shared header,
or propagate an unverified layout to other functions.

Private same-checkpoint test results:

- 30 saved near misses: 4 functions admitted 5 inferred-record candidates. All compiled; all retained
  their baseline score; **no new matches or improvements** were found in that sample.
- Two older snapshots of the large function: 4 further inferred-record candidates compiled but did
  not improve scores 950 or 210. Inferring all visible fields is not equivalent to recovering the
  original source's actual record/alias structure.
- These neutral outcomes argue for bounded trials and measured prioritisation, not a blanket promise
  that inferred structures will increase progress.

449 unclassified captured repairs were triaged locally: 66 already arose from recorded automatic
paths, 4 were duplicate evidence, and 379 remain reviewed for further investigation. Category labels
summarise source differences; they do not isolate causes. The largest source-pattern groups included
address substitutions and source-line layout changes. Mixed edits and missing failed baselines are
retained. Source/layout/flags must be replayed independently before adding another automatic rule.

## Follow-up: transcript-driven agent efficiency

A local audit of 12 recent sessions with compiles recorded 155 compiles and one match. This is an observation,
not a controlled model comparison: selected functions and histories differed. Of 16,913,071 recorded total
tokens, 16,280,168 were cache reads; do not present that total as fresh input or billed API cost.

Three machine-generated repair batches emitted 18 compile errors against unbuildable starting C. Generated
batches now preflight the baseline once and cache its diagnostic under source/build/target/claim scope.
New inputs, a new claim or a subsequent explicit compile allow another check. Explicit authored patches
remain available to fix the C. Synthetic ROM and imported-mode regressions verify one baseline invocation
and zero generated trials, rather than six repeated failures. A private replay of the actual invalid
transcript draft also produced one real compiler invocation across two requests, with zero variants.
Normal exact judging is unchanged.

The agent runner's local-family list previously omitted the new shared copy/scope/layout families. It now
uses those shared generators and profile/mismatch priorities, and trains only measured, non-cached outcomes.
This closes a tool-availability gap; no matching-rate increase has yet been measured after installation.

Compact project facts previously spent their bounded response on address-range overrides before useful
progress information. They now summarise range counts and retain progress. Repeated identical claim packets
are suppressed only after normal claim/lease handling, and a new lease or changed packet gets the full brief.

Two inspected cross-region agent matches only substituted global symbol identifiers. A normally verified
agent success can now finish a recorded donor transfer and feed the existing mapping mechanism. This requires
unambiguous donor provenance, unchanged target/donor stamps, symbol-only token edits and resolvable symbol
names. Comments alone, literal/operator/type/local-variable edits, and reopened donors cannot teach this
shortcut. Existing independent-pair requirements still apply before data mappings are reused elsewhere.

## KMC GCC 2.7.2: floating product intermediate placement

**Controlled regression of an existing captured match:** 32-bit big-endian MIPS,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Instead of storing the integer-to-float conversion in a temporary,
store the first multiplication there and consume it in the second multiplication. Keep the casts and
parenthesized product order explicit; floating reassociation is not assumed safe.

The bounded `float_product_intermediate` family generated three candidates. One recovered the successful
compiler control output; two improved the regression score from 180 to 35 and 55. The normal linked-byte
judge also accepted one exactly in a private ROM/project-state copy. This recovers an already-captured
match and adds **no new game progress**. Allocation depends on which same-type local holds the product;
the successful case reused a local whose earlier branch lifetime had ended.

The family excludes loops/gotos, calls, volatile access, address-escaped temporaries, extra uses in the
expression and later live uses. It is shared by draft repairs and agent local repairs in both judge modes.
An intermediate store can change rounding; every proposed match still requires the normal exact judge.

**Parser regression:** a homemade typed field macro using `(*(t*)((u8*)(p)+(o)))` compiled to identical
normalized KMC function bytes before/after expansion and yielded variants from the installed permuter.
This fixes parser coverage, not evidence of extra matched functions. Three other captured repair replays
found no control recovery under 12 trials each; offline replay reports them without applying candidates.

## Captured pointer, initialization and terminal-guard regressions

**Controlled recoveries of existing captures**, KMC GCC 2.7.2, big-endian MIPS,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`:

- Advancing an existing dead pointer cursor for the first dereference, then copying it into a differently
  typed pointer local, recovered the captured pointer-chase match.
- Swapping two independent scalar zero initializations recovered an allocation/scheduling near miss.
  Grouped local declarations must be understood before identifying legal scalar candidates.
- Replacing a terminal negative conditional (with a dead boolean copy) with an early return using an
  existing initialized result local recovered the captured control-flow match.

**IDO 5.3 controlled recovery**, big-endian MIPS,
`-O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`: repeating a prior member assignment after an
intervening same-object flag store recovered its captured match. A second placement compiled but did
not match. Repeating stores is a scheduling hypothesis, not a generic optimization or semantic guarantee;
volatile accesses, calls and complex expressions are excluded from these proposals.

All four generated families recovered their successful compiler controls, and separate normal linked-byte
checks in private project/ROM copies accepted one candidate per case. These are regression recoveries,
not new matched functions. Only the destination's normal judge may adopt them in future work.

**Evidence correction:** random rewrite context can flow into cascaded cross-function matches. At audit
time, 436 of 444 random-labelled captures had a different candidate hash; six of the eight remaining
captures also had a different recorded baseline. They cannot establish a direct rewrite cause. The
source-matching repeated-store capture above had the matching base; the other direct case was already
recovered by `table_slot_lifetime`. Preserve inherited origin metadata, but do not train a new rule from
the final rewrite name alone. Historical reviews remain unchanged.

## Wrapper addresses, scalar globals across calls and split mask stores

**Controlled recoveries of existing private captures**, SN-style assembler GCC 2.8.1
(`gcc-2.8.1-pm-sn-as`), big-endian MIPS, `-G0 -mips3 -mgp32 -mfp32 -O2`:

- Eight tiny wrappers shared their instruction shape and callee with verified wrappers; only one
  global-address HI16/LO16 pair differed. Instantiating donor C with the destination's global address
  recovered all eight through normal linked-ROM-byte checks. Some addresses were below `0x80000000`;
  rejecting every such address misses valid segment-address hypotheses. Restrict discovery to one
  adjacent pair, a fixed callee and the same instruction/register shape, not a broad numeric replacement.
- Keeping a signed byte global's address in a pointer across a call, loading through that pointer and
  narrowing the call result before storing it, recovered one captured match. The old scalar temporary
  can hold the narrowed result; an extra result local was unnecessary. Three storage-class alternatives
  were neutral in the controlled replay. General alias analysis is not established by this example.
- Replacing one assignment combining two AND masks with two ordered compound stores recovered one
  captured match. Both the original integer lvalue and an unsigned-cast variant reproduced the control
  output and passed the normal linked-byte judge. Other source changes in the recorded successful C
  were unnecessary for this recovery; two address-temporary alternatives were neutral.

These ten cases are regressions of already matched functions, not ten new matches or a measured token
saving. Scalar and mask proposals share both judge modes; the initial wrapper index is MIPS ROM-only.

**Agent scheduling:** an optional local pass can try bounded applicable source variants before returning
an agent's compile result. Its ordinary judge, scoped cache and stall limit remain authoritative. Record
its family plus before/after source hashes separately from model submissions; do not attribute an
inherited cascaded match to the triggering rewrite. No claim of lower token use is made until measured
on comparable live sessions.

## Scheduling experiments and durable verification

Retrospective private KMC GCC 2.7.2 controls (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`)
from the long V-Rally investigation reproduced these gains:

- Byte-pointer address association: score 205 → 195, one candidate.
- Moving a constant local reset into the first call argument, along proven CFG paths:
  controlled regression 945 → 155, three candidates. Existing masked-value reuse then
  produced exact bytes. The local sequence took about two seconds on this host.
- Explicit experimental signed-halfword inline bitfield storage: 214 → 190, one candidate.

These are controlled recoveries, not additional game progress or a fresh end-to-end speed benchmark.
Frame size, saved-register slots and local stack operands must be compared separately. A larger
frame alone does not establish the correct local layout. Bitfield storage remains explicit opt-in:
compiler layout and integer rules make it unsuitable as a default repair.

Local experiments record actual edited spans, time and function-output fingerprints. No-edit
proposals skip compilation. Eight trials with no improvement and at least 80% no edits,
identical output or regressions lower a family's priority for that input; exploration remains.
Source and build-input changes invalidate this evidence. Function-output equality says nothing
about unrelated data sections and never substitutes for the normal judge.

Exact judgments now retain a [portable verification bundle](match-verification-bundles.md).
Historical evidence helps explain regressions without silently restoring matched status or flags.

### Connected recipe replay

The scheduling control now trains a source-free two-step recipe only after an exact judgment.
A normal integrated local search recovered 945 → 155 → exact in nine compiles including its
baseline (7.81 seconds). Resetting the same isolated checkpoint, retaining the learned sequence
and clearing the miss cache recovered it in five compiles (7.63 seconds). Family prioritization
reduced compiles; this small control does not establish a wall-clock or agent-token improvement.
See [learned repair search](learned-repair-search.md) for scope, exploration and rotation rules.

## Source repair followed by bounded profile checks (31 October 2026)

Evidence: ten new source-assisted live-API matches in a retail MIPS II game, totaling 1,988 bytes; all ten were selected in a full byte-identical cartridge rebuild. Private game data is not part of these regressions. The observed compiler was IDO 5.3 with `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`; optimization was function-scoped. This identifies working profiles, not the unique original compiler release.

- A corrected four-argument sound wrapper and a typed voice-release routine still differed at `-O2`; the repaired candidates matched at `-O3`. A saved FX-mix candidate matched when its profile was changed to `-O3`, before the next attempt. Check relevant flags again after a source improvement rather than exhausting mutations under the old profile.
- Typed sequence/player/marker records, real audio-command macros, original local declarations and source line layout recovered SDK routines. A pool-constructor draft had mistaken an integer allocation size for a pointer. Prefer explicitly configured reference bodies and headers before mutating raw casts. No reference source is downloaded or bundled by this feature.
- A typed integer volume expression recovered a match after restoring original multiplication order and widths. Avoid floating-point reassociation and guessed casts. Automated product-order proposals are limited to declared unsigned scalar operands.
- Restoring the original equality-comparison operand order changed the final allocation/scheduling in a controller-pak checker from score 35 to 0 at `-O1`. Operand order is a bounded hypothesis, not an equivalence claim for volatile or side-effecting expressions.
- The supplied voice-release source cleared a pointer on only one branch, whereas retail clears it after both paths. Source identity is advisory: the ordinary linked-byte judge remains mandatory.

Controlled tests cover claim release/reacquisition, concurrent workers, source-improvement/profile sequencing, source mapping selection, side-effect exclusions and explicit profile-induced match reporting. They do not establish a new automatic match-rate benchmark.

### Captured repair follow-ups: control flow, stores and call types

Same-settings captured matches motivated bounded shared-return-tail, direct compound-store, redundant
counted-loop-guard and float-bit-argument/prototype hypotheses. Named compiler-runtime casts/modulo also
get explicit scalar-width candidates. Captures may include other function/declaration edits: a matching
source pattern alone does not establish the cause. Function-specific and supporting diffs are now separate.

Controlled regression evidence: pinned GCC 2.7.2 KMC, `-O2`, synthetic C controls for compound stores,
shared return tails and paired float-constant/prototype changes produce the expected extracted function
bytes. This checks candidate generation/compilation, not a new game match or a measured family success rate.
The ordinary linked-byte/objdiff judge is still required for every application. Complex lvalues, volatile
code, conflicting declarations, indirect/variadic call prototypes and unrecognised runtime names are excluded.


## Runtime width, SDK profile priority and interior entry addresses (31 October 2026)

Observed evidence: ten further source-assisted exact matches in a retail MIPS game, totaling 2,560 bytes, selected in a full byte-identical cartridge rebuild. These observations guide bounded hypotheses; the new regression tests are synthetic and do not claim additional real-game progress.

- A signed 64-bit modulo helper and double-to-unsigned-64 conversion had incorrect-width drafts. Their targets contain MIPS III operations and O32 argument-home-slot or paired-return evidence. Native 64-bit C matched with IDO 5.3 `-O1 -mips3 -32 -G 0 -non_shared -Xcpluscomm`. The profile preflight now prioritizes this profile only for recognized instruction shapes, and can probe a generic native-width arithmetic candidate in the same four-compile allowance. Source-changing results use the existing compiler/source suggestion and revalidation workflow; recognizing a shape is not proof of helper identity.
- Six timer/thread/messaging routines matched with IDO 5.3 `-O1 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul` after original types/register locals/body were restored. Recorded SDK-signature naming evidence now prioritizes `-O1` after the current-profile baseline. A familiar user-entered name alone does not trigger that priority. This is a working profile, not unique toolchain identification.
- A thread constructor scored 225 when its cleanup address was expressed as another function's entry plus an offset. The compiler emitted an additional address addition. Referring directly to the proven target entry yielded score zero. Interior-entry repair requires agreeing target LUI/ADDIU HI/LO expressions, a known numeric function-address label and byte-pointer stride. It introduces a candidate-local address declaration; it never moves code, splits functions or changes the global symbol table.
- Sequence-reader attempts remained unmatched with isolated ordinary-ABI calls. Target temporaries surviving calls suggest whole-unit/static-helper register specialization. Adding static helper bodies did not produce acceptable isolated matches and should not be repeated as a presumed fix. The app now reports this possibility as an advisory, not an ABI override or a reason to count a match.

Compiler settings are still judged and applied through existing gates, and no proprietary toolchains or reference sources are fetched. Imported projects retain their own build/compiler workflow.

## Original object definitions and conditional source extraction (31 October 2026)

Observed evidence: ten further source-assisted exact matches in a retail MIPS game, totaling 2,728 bytes, passed a full byte-identical cartridge rebuild. Eight routines used IDO 5.3 `-O1 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`; two matrix converters retained that profile at `-O2`.

Two device-manager candidates scored 2,110 and 2,950 with their original bodies/types but extern-only global declarations. Restoring the original object definitions recovered exact code and removed repeated LUI address loads between field stores. A timer initializer likewise changed from 515 to zero after restoring its current-time object definition. The observations do not uniquely establish the compiler/assembler mechanism; do not assume every global should be redefined or automatically invent alignment.

Local source preparation now accepts an explicit list of reference object definitions. Only referenced selected objects (including macro references) enter the candidate. The definition-preserving version precedes the version without those definitions, under one budget and the ordinary code/data judge. Original definition/body line numbers are retained; source-changing headers still use existing promotion guards. Export and complete-image verification remain separate required gates. No global symbol-table entries or placement rules are invented.

A timer function extraction failed because alternate preprocessor branches had different brace counts. Branch selection now uses configured numeric macros before locating the function, blanking inactive text/directives without removing newlines. This is deliberately not a complete C preprocessor: unsupported expressions and unknown compiler-specific conditions require numeric overrides or a prepared subset. No reference scripts or external preprocessor commands execute.

Three controller routines and both matrix converters recovered with original typed records and ordinary loops instead of accumulated draft mutations. Existing typed-reference preference remains appropriate; exact judges, not source resemblance, decide progress. Controlled tests of these new preparation paths do not claim a new automatic matching rate.

### Original SDK context before broad permutation

A source-assisted N64 batch recovered ten additional routines / 5,304 bytes using IDO 5.3
`-O1 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`, with a byte-identical full cartridge rebuild.
Original controller/directory records, the inode byte/halfword union, macros and locals matched directly.
One initializer reached score 20 with only two unresolved `__ull_div` calls; explicit unsigned 64-bit calls to
an existing helper at the target's two call sites produced score zero. This establishes a verified mapping
for those sites, not permission to assign that name/ABI to every unknown function.

Preparation now carries referenced typedefs/macros from the explicitly mapped file into its private header.
The bounded ROM compiler preflight prioritizes up to two distinct profiles from nearby exact matches of the
same installed toolchain. These remain hypotheses: normal per-function judging and compiler-setting approval
rules apply. Original source context is tried before profile testing, so retained improvements are checked
with their promoted private header. Imported builds still supply their own compatible headers/settings.
Helper-only diff diagnostics suggest mappings only when every other compared instruction agrees; they never
write addresses, alter objects or bypass the ordinary judge. Tests cover this behavior with synthetic evidence;
these implementation tests are separate from the ten observed matches above.

### Inline boundaries, temporary placement and loop shape in a large MIPS function

A private 820-instruction function matched with KMC GCC 2.7.2, flags
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The original saved draft scored 6,602.
The successful source combined inline scalar helpers, temporary placement, stack-address
locals, load/store ordering, a structured outer loop, invariant calculations and array storage.
This is evidence for the combination, not proof that every edit was necessary.

A controlled isolated regression retained the successful source except for six inline bounds
comparisons. It scored 3,545 and failed linked comparison. The new grouped inline-comparison
proposal recovered an **exact linked verdict**; individual-site proposals did not. Both the
original successful source and recovered candidate displayed a raw disassembly score of 80 in
this isolated harness while their linked verdict was exact. Treat linked equality as authoritative;
do not infer acceptance from the raw score. This replays an existing match, not a new recovery.
Game source and compiler artifacts remain outside the repository; committed tests use synthetic C.

The shared MIPS draft pipeline now proposes bounded typed inline identities/comparisons/float
products, scalar expression temporaries, independent scalar assignment order, array-address
locals, isolated backward-edge loops and first-statement loop invariants. Up to four cross-family
bridges feed the existing beam, which already retains bounded worse/identical-output alternatives.
These are hypotheses compiled by the ordinary judge, never automatic semantic or match claims.
Qualified storage, assembly, unsafe control flow and nonlocal scalar operands are excluded.
Array extents are preserved; existing measured frame-growth repairs handle frame-size hypotheses.
No out-of-line helper is accepted unless the ordinary linked comparison succeeds.

The original draft was rechecked at 6,602, but recovery from that entire draft has **not** been
established for the new search. Avoid spending repeated agent turns on one comparison at a time:
try a bounded consistent helper shape across repeated axis/bounds tests before escalating.

### Library formatter structure and interacting local repairs

An 819-instruction MIPS formatter matched using KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The historical best draft needed a prototype
repair to compile against the current header; its old score was not a valid current baseline.
A related local formatter and a public KMC implementation supplied structural clues. No library
source or game data is bundled with the app; library reuse uses explicitly imported local objects/C.

Measured stages included case layout (13,833 → 10,359), separate character/length locals and
conditional output shape (9,075 → 5,815), a switch-only flag-loop backedge (5,815 → 3,325),
and duplicated short floating tails (3,325 → 2,035). These stages include multiple changes and
are not isolated attribution claims. Signed output characters are required; an earlier candidate
with unsigned output was rejected as a trustworthy baseline even though it scored better.

Controlled replay on a private project/database copy starts from the existing score-176 attempt.
The bounded declaration/initialization bundle recovered an exact linked verdict on proposal 3 of
19 generated proposals. This recovers an already-known match; it does not count as a new live match
or establish a general success rate. A raw instruction score of 100 can still have an exact linked
verdict because relocation display labels differ: the linked byte judge remains authoritative.
The successful original source also passed the complete cartridge rebuild before publication.

New shared C hypotheses cover adjacent load/condition assignments, discarded-result conditional
output macros, switch-only flag loops, short shared tails and unqualified scalar declaration/
initialization order. Small cross-family bundles retain interacting choices even when an individual
step scores worse. Switch-order proposals read bounded original jump tables and preserve entire
fallthrough groups; C declarations at case scope and unsupported labels are rejected. All proposals
still go through the ordinary compiler and destination byte/objdiff judge.

Library opcode similarity is an advisory lead, never an exact identity or automatic rename. Imported
local C retains unconditional macro dependencies. Saved-score records now include a scoring-code
fingerprint; the function inspector explains missing/changed compiler, header and scorer evidence,
and links to the current Diff measurement. No historical score is silently treated as current.

A separate replay of the new target-table ordering rule improved the earlier score-13,833 draft
to 12,104 without other edits. It remained unmatched. This is distinct from the larger manually
adapted case-layout stage above; do not attribute the whole 13,833 → 10,359 improvement to this rule.

### Math prototypes, allocation extents and prepared-source flag checks

A further source-assisted N64 batch recovered ten routines / 6,008 bytes and passed the complete original-cartridge
comparison. Two floating matrix builders reached call-only differences at IDO 5.3 `-O2`; target-supported math
aliases needed explicit float argument/return declarations, not merely renamed calls. An implicit-int declaration
changes the ABI. Helper-only diagnostics now include known unary SDK math names and their expected prototypes,
while keeping the existing requirement that all other compared instructions agree. They never assign symbols.

A synthesizer initializer reached score 10 with a sole allocation immediate of 76 in the target versus 72 from
reference `sizeof(ALAuxBus)`. Recording that target-observed allocation extent matched at `-O2`; it did not establish
an unknown field or justify changing existing offsets. A sole immediate difference near a known allocation call
now produces an advisory comparison, not an automatic layout rewrite.

A buffer-save routine matched its improved original source at `-O3` after remaining unmatched at `-O2`. Normal local
source preparation now checks up to two compiler profiles immediately after a saved improvement, using the promoted
header, remaining pass budget/deadline and the existing settings approval/rejudge gates. Claims are released first;
imported builds keep their own settings. First compiler errors are summarized before cascades, with complete logs
stored privately under the app workspace rather than included in portable projects. Synthetic regressions verify
these changes separately from the observed matches above.

## Candidate-local call, alias and lifetime repairs (31 October 2026)

A 775-instruction MIPS routine matched with KMC GCC 2.7.2, using
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The normal linked judge accepted all 3,100 bytes;
the standalone function check and complete normalized cartridge comparison also passed.

Reusable observations from the investigation:

- A candidate-owned `void callee(void)` declaration hid an argument explicitly loaded into
  `$a0` in the target call's delay slot. Correct both the local declaration and call. Do not
  infer no arguments from the existing candidate's prototype.
- An auto-named global and a byte-array base plus a constant denoted the same address.
  Expressing both reads through one base let this compiler reuse loads. Numeric-name
  resolution is only a hypothesis; the destination linker must verify its addresses.
- A single-field record can help even when each distinct call-result local accesses just
  one field. Sharing that record shape recovered scheduling not reached by per-local
  multi-field layout inference. Conversely, lowering one inferred record field to a raw
  access corrected an unwanted store motion. Neither shape is universally better.
- Repeated byte masks feeding only boolean conditions can prolong a temporary's lifetime.
  Direct conditions, a separate call-result local and reuse of an earlier dead compatible
  result slot are complementary source-shape hypotheses. Splitting is not always the
  winning choice: the final manual matrix scheduling fix reused an earlier result slot.
- Exact linked verdicts take precedence over symbolic display scores. Equivalent relocation
  spellings in this controlled replay can leave a nonzero displayed diff even when the
  linked bytes are exact; the ordinary engine records exact matches as score zero.

The new source families are replayed against a private copy of the saved pre-match draft.
Results below are controlled recovery experiments, not newly discovered live matches.
Game source, ROM, database and generated replay artifacts stay outside this repository.

Final controlled recovery: starting displayed score **1,810**, the bounded source generator
recovered an **exact linked match in eight greedy refinement rounds / 175 compilations**.
Each round tested at most 32 proposals, retaining the best judged improvement; exactness came
from the original linked-byte checker, not the displayed score. The first narrower run stopped
at 275; adding dead-result reuse, field-value lifetimes and target-observed load staging supplied
the remaining steps. This is a private regression replay of an already-known match, not a
claim that Autopilot finds every comparable routine within that budget. No global search
budget, beam depth or compiler setting was enlarged for this change.

### Captured scalar and floating lifetime repairs

Private isolated replays of two same-settings captures recovered their recorded successful compiler output
with `cast_bitwise_lifetime` and `float_cast_lifetime`: SN64 GCC 2.8.1 (`sn-n64-281`),
`-G8 -mips3 -O2 -mgas -meb -mcpu=VR4300 -mhard-float -mfp64`. Separating an integer cast from
its constant bitwise operation and explicitly assigning a floating cast before the first multiplication can
change register allocation. Preserve factor order; neither float reassociation nor semantic equivalence is
assumed. An alternative floating split regressed, so the exact judge must select candidates.

A selected unsigned shift/store capture was already recovered by `typed_temporary` with KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Do not attribute that recovery to the new restricted shift-store family.
That new family excludes live locals, loops/gotos and volatile code. Synthetic KMC `-O2` output controls
cover all three families. These are compiler-output regression recoveries, not additional verified game
matches; game source and exports remain private.

### Reference SDK compatibility and helper context (31 October 2026)

A subsequent source-assisted IDO 5.3 batch recovered ten routines / 5,688 bytes with a complete original-cartridge
comparison. Eight matched with `-O3`, one with `-O2` and one with `-O1`; all used
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`. These are verified working profiles, not uniquely identified
original settings. A documented callback member in an older SDK explained an allocation-size discrepancy;
arbitrary padding was unnecessary. Missing word-operation macros could compile as unintended implicit calls.
Private float-return helper declarations changed the ABI. A math routine retained union-based bit constants and
original expression order and passed both the byte judge and complete-image rebuild. Static helper context
improved other drafts without matching them. Calls in loop increments also exposed an extractor false positive.

The app now offers explicitly paired source/header alternatives, selected static helper definitions and actual
same-file prototypes; it warns about incompatible typedefs, missing macro-like calls and implicit declarations.
Original selected object initializers are retained verbatim. A bounded stale-input retry recomputes destination
state and never bypasses claim or promotion guards. Synthetic tests verify these mechanisms independently of
the private manual batch; they are not a claim that the new code automatically recovered those ten functions.

### SDK I/O context at IDO 5.3 -O1 (31 October 2026)

The next private source-assisted batch recovered ten SDK I/O, controller and pack routines / 4,928 bytes through
the live API's ordinary linked judge. All use IDO 5.3
`-O1 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`. Several started with `-O2` drafts; changing optimization
alone did not match them. Original declarations, aggregate alignment, explicit register locals, numeric volatile
MMIO macros and target-supported retail control-flow changes were also required.

In a controlled buffer-submission candidate, an address-of-symbol MMIO form emitted an extra address instruction;
a direct scalar-symbol alternative selected a different base register. Original numeric volatile MMIO accesses
matched. Equivalent symbolic diff spellings must not override the linked verdict, and an address must be proven
from the target before any such candidate is tried. Two byte-aligned controller response decoders matched their
original record-copy shapes with distinct target buffers. A reference disk revision also had different polling
masks, error values and recovery paths; source identity was not sufficient evidence for those details.

The proposed preparation changes in this PR were not installed for the matching batch. These are manually
verified source/context findings, separate from synthetic feature regressions and later whole-image export checks.

### KMC GCC: local draw-pair lifetimes (1 November 2026)

**Verified case:** an 845-instruction / 3,380-byte rendering routine matched with
`gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. A standalone ordinary judge
confirmed equality of every linked byte, then the live app accepted the same source as matched,
score zero. A subsequent repository sync also reproduced the complete 12,582,912-byte cartridge,
retaining all earlier selected C. This is a manually verified case, not an automatic repair family.
Private evidence is retained outside this repository in `func_80076060-matched.json`,
`func_80076060-matched-warnings.txt` and `live-exact-verification.json`; source SHA256
`82858b9b4ee8237c496a715f7d82c8f7d48170fec659739043d55e71f8e2bf8a`.

- The final near miss had the correct length, addresses and instruction order, but eight register
  operands differed. Compiler dumps showed a short-lived constant receiving the saved register
  wanted by a converted coordinate. The reference allocator ranks quantities using references,
  size and live length; a source variable's apparent lifetime alone does not describe its lowered temporaries.
- Replacing a shared draw-pair tail with separate lexical blocks in the two incoming paths,
  each with its own local coordinate, and explicitly staging the second call's context, handle,
  coordinate and Y arguments recovered the exact output. The compiler merged their common tail.
  Both a direct narrow-coordinate form and an explicit high-word form matched. This is evidence
  for the combined source shape, not a rule that duplicating code or adding braces always helps.
- An inline-helper alternative recovered the desired registers but enlarged the frame by eight
  bytes and reordered instructions. Equivalent local blocks avoided those differences. Adding
  more helper arguments, changing their order or widening Y did not fix that alternative.
- An earlier controlled step retained an accepted halfword field in a separate local and used
  it for both calls' flag arguments. This recovered six register operands that constant reuse
  had changed. Narrowing was performed only after the original full-width comparison accepted
  the value; narrowing before that comparison would change behaviour.
- Broad declaration/type changes, constant spellings, equivalent argument arithmetic and
  repeated identical branches did not recover the last eight operands. The final source passed
  an uninitialized-value warning check and used neither assembly nor hard register bindings.

Relocation-label discrepancies remained in a symbolic diagnostic even for the exact candidate.
The unchanged linked-byte verdict and independent byte-buffer comparison established exactness;
altering target addresses or accepting the diagnostic score would not.

### Bounded follow-ups from the SDK I/O batch

The ten-function IDO 5.3 `-O1` batch above motivates raw-word-supported I/O profile priorities, MMIO expression
hypotheses and local status storage alternatives. Existing byte-only records can recover aggregate-copy shape
when target unaligned word copies support it. Reference constants and recovery branches are reported for review,
not rewritten automatically. Source/header improvements reuse digest-scoped preflight and its existing budget.
Synthetic tests distinguish machine-word address/width evidence from symbolic names, reject shadowed/unqualified
objects and address-taken locals, and require real record declarations. These are feature regressions, separate
from the original private matches and from any later live recovery measurements.

### Follow-up: object definitions, address expressions and typed records

A subsequent manual live-API batch recovered five previously unmatched entries / 2,600 bytes. Three SDK
initialization/video entries matched IDO 5.3 `-O1`, an integer-formatting entry matched `-O2`, and a typed
hash-iterator entry matched `-O3`, with the common `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul` flags.
These are manual recoveries with the ordinary judge, not automatic wins attributed to this uninstalled PR.

- Restoring the original handle and clock-rate object definitions changed address grouping and scheduling;
  replacing definitions with `extern` was not neutral. Export still has to verify their initialized/BSS placement.
- A named buffer reference selected `addiu` where a literal-address cast selected `ori`. Both denoted the same
  address, but only the named expression reproduced the target bytes. Symbolic diff annotations alone do not
  establish an instruction difference: inspect the linked encoding.
- Explicit, correctly typed calls to the already located unsigned 64-bit divide/remainder helpers resolved an
  integer-formatting call mismatch. This is specific ABI evidence, not permission to invent helper symbols.
- Original hash record/pointer declarations and loop structure recovered an iterator whose raw-offset draft
  differed in register allocation. A nearly matching draft is not a substitute for checking original types.

A video-manager draft still differed by unreachable NOPs; debug-flag probes did not recover it. Other private
helper fragments entered with live saved/floating registers, so their isolated ordinary-C signatures remain
unproven. Neither pattern is resolved by adding padding instructions, weakening boundaries or changing the judge.

### Source-context follow-ups

The five-match/2,600-byte live batch above motivates preserving unambiguous referenced definitions from the
explicitly mapped source file, while retaining the plain context as a bounded alternative. This does not search
other files or invent initializers/layouts. Definition/data promotion still uses the ordinary linked judge and
standalone placement checks. Existing preparation/profile digests already include source, header and build state;
regression tests cover retrying after each changes.

Named-buffer alternatives now require an existing byte-array declaration and raw MIPS LUI/address evidence;
scalar constants, arbitrary names and unproven addresses are not rewritten. Runtime-helper call-only diagnostics
include signed/unsigned 64-bit prototype candidates for ABI review, never automatic symbol installation.
Read-before-definition saved registers, mixed floating/pointer entry usage and target-only NOPs are advisory
context/boundary leads, not authority to change signatures or insert padding. Original records and control flow
are recommended ahead of broad searches when differences are only register allocation.

A rejected partial compiler suggestion now keeps the working profile and returns to source search. Unexpected
errors and cancellation are not swallowed. These synthetic regressions are separate from manual game matches;
this change has not been installed into the running app during development.
### Bounded automation from the KMC local-lifetime case

The verified `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3` draw-pair case above
motivates three shared C hypothesis families: `branch_tail_lifetimes`,
`staged_call_arguments` and `combined_call_lifetimes`. The first duplicates a terminal,
straight-line call pair into two existing arms with separate integer locals. Staging uses
only known unescaped integer locals and signed-int literals; combinations are capped at eight.
This deliberately covers a narrow source shape, not every shared-tail rendering routine.
Qualified objects, ambiguous types, macros and escaping/jumping control flow are left alone.

Register-only rows, including indirect-call register changes, prioritise these families.
After ordinary judging, a non-improving detour that grows both a previously correct frame
and the instruction count without improving instruction structure is not expanded further.
Unknown layouts and numeric improvements remain eligible. This is bounded search pruning,
not an equivalence proof or permission to skip the exact judge.

Synthetic execution regressions compare call values/order for both arms and varied signed
inputs using a host compiler. Search tests cover frame-growth pruning, exact verdict precedence,
input-scoped failure caching and retry after changed build inputs. These are controlled feature
regressions; no additional private-game matches or token savings are claimed for these families.
The linked-byte verdict is reported separately from symbolic annotations. Known generated
compile errors now join the existing exact-source miss cache, while infrastructure failures
stay retryable; compact agent summaries expose both failures and regressions.

### Normalization lifetimes and partial frame gaps (1 November 2026)

A private 808-instruction KMC GCC 2.7.2 routine was verified exact with the unchanged
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` profile. The final steps isolate three reusable shapes:

- A signed-halfword normalization, followed by a void setter, can be expressed as an inline
  helper that performs the setter and returns the normalized value. Keeping that value live
  across the call recovered the last two register operands. No extra call was emitted and the
  callee prototype stayed unchanged. Pure expression helpers had not recovered this allocation.
- A variable used first as a full-width loop stride and later only through signed-halfword reads
  benefited from a separate narrow saved value. The automatic repair mirrors earlier assignments
  into the new value, including paths that skip the later phase; an initial zero alone is not a
  general correctness guarantee. Address escapes, compound updates and unsupported syntax are skipped.
- The target frame was 48 bytes larger, but the first thirteen stack slots already agreed.
  Six late narrowing helpers before low-bit masks reserved six unused eight-byte spill slots,
  preserving the instruction sequence and aligning the final two spills. A diagnostic compiler
  built from the pinned compiler's source reproduced the installed compiler's output and traced
  a nonzero reference count for a pseudo eliminated by combination. This explains the observed
  unused storage; it does not establish a universal frame-padding rule.

`call_return_normalization` and `split_narrow_lifetime` feed the shared local repair pipeline.
`spill_normalization` requires a consistent positive MIPS frame gap, unchanged earlier stack
accesses and local integer values immediately masked to the low fifteen bits or fewer. It tries
at most eight bundles, including the inferred slot count, without changing masks or array extents.
The per-function deep search, background local repair, agent local experiments and iterative
phase use the same bounded proposals and ordinary judge. Other architectures can use the scalar
lifetime repairs; the frame hypothesis currently requires MIPS stack-offset evidence.

A controlled private replay using generated candidates recovered **141 → 115 → 110 → exact**
in ten candidate compiles (eight frame candidates, one lifetime split, one call/return helper).
This reproduces an already established match, not ten new matches or a measured success rate.
Committed tests use synthetic C, including skipped-phase, negative-value and mask-preservation cases.

Equal numeric scores can hide useful frame progress: each additional spill slot reduced offset
error without reducing the mismatch count. The frontiers now use stack-offset distance as an
advisory tie-breaker. The iterative beam's allowance of two source shapes with identical compiler
output applies at each depth rather than being consumed across the whole search. Width, depth,
compile/time budgets, exact-output evidence and normal linked-byte acceptance remain mandatory.

### Same-width zero-test reuse and pointer-return staging

Recent same-settings KMC GCC 2.7.2 captures (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`) motivated two bounded
hypotheses: reuse an unused signed/unsigned 32-bit local only for a copied pre-decrement value's zero test;
and stage a nested no-argument call only with an actual pointer-return prototype and an unused pointer local.
The latter capture also moved declarations, so staging alone is not claimed to be the sole cause.
Synthetic pinned KMC `-O2` controls verify generated function bytes. These controls are not new game matches.
Macro calls and qualified code are excluded from staging; no pointer return type is inferred.

Source-assisted aggregate-parameter, whole-struct snapshot and graphics-macro findings remain reference
context evidence. Preserve real source/header pairs and macro single-evaluation shapes; do not synthesize
aggregate layouts or flatten side-effecting macro invocations into presumed equivalent stores.

Private isolated replay of the zero-test capture recovered its recorded successful compiler output using
`word_zero_test_reuse`. The pointer-call capture was not recovered by staging alone within the sampled
budget and remains unresolved. Both results are replay evidence against recorded controls, not new ROM
matches or claims that the complete historical edit has been automated.

### Prepared context may need flags before it improves

A source-assisted MIPS/IDO session found original typed candidates that were worse under the working
`-O2` profile but byte-identical under `-O3`. Do not restrict profile probes to already promoted
source/context improvements. Preparation now spends at most two remaining compile-budget slots on
private-context profiles even when the current-profile candidate loses. It stores source, header,
profile and the ordinary link/data verdict together as review evidence; it does not silently promote
that context or change compiler settings. A synthetic regression verifies an exact alternate-profile
verdict with unchanged working settings. This test establishes the control flow, not a new game match.

### Constant indices, reference balancing and evidence reduction (1 November 2026)

Further controlled replays from the same private KMC checkpoint support two additional shapes:

- A zero used as an array-store index changed allocation where a dead zero assignment did not.
  `constant_index_temporary` tries function-local and block-local zero indices on literal stores,
  plus two small grouped pairs (at most ten candidates). It preserves the pointer, literal, store
  order and index value. Generated candidates replayed **1846 → 576**, and a pair reached **291**.
- Identical conditional call arms can change compiler reference counts. `balanced_call_references`
  tries initialized, unescaped local guards on individual and paired related calls, up to sixteen
  candidates, fairly interleaved between two guards. Each original call still executes exactly once
  in its original position. A controlled regression replayed **201 → 151** under fixed settings.

These reproduce existing improvements; they are not new live matches or a compiler-independent success
rate. No game names, addresses or source patterns are embedded in the generators. Ordinary compilation
and exact judging decide every proposed repair. Family demotion now resets for a changed parent source,
while exact compiled-miss caching and the existing source/build-scoped measurement history remain intact.

Repair replay adds optional bounded edit ablation and installed-GCC phase comparison, with downloadable
private reports. A private replay removed an irrelevant comment while retaining the two changes needed
for the successful output in the tested decomposition. Native GCC diagnostic output agreed with normal
output across thirteen captured phases. Neither phase statistics nor ablation results replace the linked
judge; allocated static helper code, initialized data, BSS sizes and named relocations are included in the
frozen-output comparison. See `docs/repair-replay.md` for limits and controls.

### Narrow increment expression shapes (1 November 2026)

During an unfinished large MIPS routine investigation with KMC GCC 2.7.2 and
`-G0 -mips3 -mgp32 -mfp32 -O2 -fno-strength-reduce -msoft-float`, a short-counter
prefix increment recovered a target loop-tail register pattern. A postincrement
embedded in a field store and one in an address expression recovered additional
register choices. Reviewed live candidates improved from displayed score 667 to
622 across several edits; the routine is still unmatched. This is partial evidence,
not a new exact function match or proof of the original source spelling.

The shared generator now proposes adjacent `i += 1; v = i;` as `v = ++i;` and
`p->field = i; i += 1;` as `p->field = i++;`, with unescaped local scalar counters.
It rejects volatile code, ambiguous statement boundaries and side-effecting lvalues.
Synthetic pinned-compiler controls verify generated shapes independently of the
private investigation. Nonadjacent motion, bucket-address postincrements, automatic
counter narrowing and frame padding are deliberately not inferred by this rule:
those need additional liveness/range evidence. Normal budgets and exact judges apply.

### Fixed-context pointer and zero-test replay (1 November 2026)

Five captured near misses were recompiled with their successful capture's exact header and
`gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2 -g3`, then tested with the unchanged linked-byte
judge against copied targets. Source/header hashes and target hashes were checked first.

- A 168-instruction routine recovered from score 60 by inlining a cached pointer load into
  its scalar field reads. The accompanying declaration reorder was not necessary.
- A 509-instruction routine recovered from score 65 by extracting a nested pointer-returning
  call into a known local. Declaration reordering was again unnecessary.
- An 82-instruction loop recovered from score 20 by reusing an unused signed word local for
  an unsigned value's zero-only test. Keep the old declaration and isolate that definition's
  live range; later loop definitions/tests can still use the original unsigned local.
- A 194-instruction routine recovered from score 300 using the existing target-proven MMIO
  family. Restoring the named MMIO write failed at 300; restoring `return (void)` on the tail
  copy still matched. The volatile MMIO expression was decisive; removing `return` was not.
- An adjacent field-store swap recovered another captured control, but its indirect RHS read
  has no proven alias relationship to the destination. The automatic field-order family leaves
  this case alone, rather than treating an exact historical edit as a general alias proof.

Against the existing narrow follow-up module, the word-zero-test control already recovered,
while the pointer-load and pointer-call controls did not. This comparison is not an exhaustive
baseline against every existing family.

These are recoveries of previously solved captures, not new game progress. Single pointer
repairs are prioritised before a bounded declaration-order cross product. Typed pointer reuse
requires CFG liveness, definite initialization and unescaped storage. Store-order trials require
known distinct non-volatile struct fields and memory-independent values. Macro captures,
stringification, uncertain types and external control-flow entries are rejected.

Compiler-output replay can now handle captures differing only in judge revision: it recompiles
both under one compiler/header and labels the comparison explicitly. Other context changes still
block replay, and compiler-output recovery never adopts a game match. The linked replays above
are stronger, separate evidence. No full-image rebuild or live adoption was performed here.

### Bounded call arithmetic, element addresses and narrowing reuse

Recent private exact-match captures under KMC GCC 2.7.2 (`-G0 -mips3 -mgp32
-mfp32 -O2 -g3`) supplied three further search hypotheses:

- Moving a local subtraction before a no-argument call fixed a recorded score of
  235. Try this only for ordinary standalone assignments and declared word locals.
- Staging an array element address before a field store fixed a score of 10.
  Use the actual element type from an explicit struct/array declaration; do not
  infer the layout. Do not reorder a right-hand-side call or mutation.
- Reusing an unsigned-short conversion across calls appeared in another match,
  together with a return-prototype correction. This is compound evidence: it does
  not establish that narrowing reuse alone caused the match. The bounded search
  only caches equal explicit conversions of an unescaped word local, across at
  most four straight-line statements without modifying that local.

Independent synthetic controls compiled with pinned KMC GCC 2.7.2 `-O2` verify
that each generated shape can reproduce the manually written control's function
bytes. These are regression controls, not new game matches or proof that any
particular live function benefits. Ordinary linking/objdiff judges remain required.

Matched callee signatures, referenced typedefs and reachable original macros now
appear as attributed advisory context in both project and ROM agent briefs.
Existing cross-game source leads also carry actual declarations/macros when
available. This supplies library context earlier without asserting an identity or
SDK version from similarity. Object-only signatures cannot supply source layouts.
No reference is silently merged into the current shared header: even exact bytes
do not establish a unique C type, and independent references can conflict.

Private replay validation additionally recovered the recorded successful function
bytes for both the arithmetic case and the element-address case under their saved
KMC flags and context. The element-address replay needed the existing correctly
typed pointer, dead after the store; a fresh block-local pointer did not recover
that output. Both alternatives remain bounded hypotheses. Existing-pointer reuse
is excluded inside enclosing loops, after address escape, or when later reads
would need its old value. No live source, compiler settings or match status was
changed by these replays.

### Review prepared source and compiler settings as one unit

Typed reference candidates may lose under the current optimization profile and become exact under a
neighboring one. Local ROM projects can now explicitly recheck and approve the complete private source,
header and function-only compiler profile. Compilation happens outside the transaction; stale inputs,
claims, overlapping entries and a non-exact verdict prevent publication. A synthetic regression tests
atomic publication and the saved verification header. This is not a new game-match claim.

When linked instructions match but initialized data differs, report the data object and differing
address range separately. Anonymous local-array initialization may emit padding that disagrees with
the following ROM object. A bounded preparation hypothesis may copy an existing named array through an
aggregate record only when its declared int extent and ROM values agree exactly. The unchanged byte/data
judge and the complete repository rebuild decide acceptance; do not trim padding checks to gain matches.

### Reduce repeated context before increasing search breadth

Three recent substantial helper transcripts contained a 455k-character compiler
trial response (450k of unrelated project configuration), 529k characters of claim
responses, and 79 unfocused assembly reads. Of 190 independent variant batches,
89 retained the starting candidate. These counts describe recorded payloads and
outcomes; they are not a causal benchmark or a measured token-price saving.

Trial retrieval now returns only the selected C/profile, result and context ID,
while its full private reproduction record remains internal. Claim queues keep
leases but defer later function details; explicit paging preserves full assembly
access. App helper batches get at most three automatic repairs before manual
variants, once per source/build scope, with normal judge and trial deduplication.
An automatic pass explicitly says when handwritten patches were not submitted.

Conversation budgets use completed tool calls and response characters, not guesses
about provider billing: 80 calls or 160k characters by default. Active tools finish
before rotation; structured handoffs retain revisions, failed-experiment history,
remaining repairs and explicit notes. Both API and CLI helpers start fresh sessions
without deleting claims, accepted matches or attempts. Measure actual provider
usage and matches per minute before claiming a cost or speed improvement.

### KMC GCC: a flat score can conceal two real address corrections

V-Rally PAL `func_8000F660` already had the right 690-instruction structure. Two
stores addressed `0x800C7040` and `0x800C6AFC` instead of the ROM's `0x800B7040`
and `0x800B6AFC`. A compound byte-array expression obscured the second address;
symbolic HI/LO display labels were insufficient evidence for either one.

An isolated replay using the recorded header and GCC 2.7.2 KMC profile
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` reproduced score **80**. Each generated single
correction removed one raw effective-address mismatch but retained score 80.
The bounded pair passed the unchanged linked-byte judge with score **0**. These
are replayed historical matches, not additional live-game progress or a token-cost
benchmark. The standalone repository separately passed its complete cartridge check.

Use raw LUI and signed memory-offset words for effective addresses, respecting
clobbers, control flow and byte order. Display numeric addresses alongside symbolic
diff operands; do not change scoring or reinterpret an annotation as the verdict.
Treat an expression-local absolute literal or proven byte-array addend as a bounded
hypothesis. Never rename a shared symbol or guess a typed-pointer stride. Try up to
four singles and six independent pairs even when singles leave similarity unchanged.
The shared draft/local/deep repair pipeline and cached autopilot address feedback
can propose these trials; only the ordinary compiler/link judge accepts them.
Other processors remain unchanged; raw MIPS decoding is not evidence for their ABIs.

### Sustained progress and comparable efficiency windows

A few tiny score reductions should not extend a paid matching session indefinitely.
The new progress window counts fresh scored attempts in one claim/build scope,
persists across restarts, and only considers handing work back after 24 trials.
If the best score of the later half of the latest 12 trials improves by at most
1% over the first half, retain best C and write an evidence-led handoff. Exact
matches and missing/error scores cannot trigger this heuristic. Explicit larger
work budgets extend the minimum. This is scheduling advice, never proof that a
function is impossible; ordinary source/build changes and future agents can resume.

Compile feedback supplies the first target/candidate mismatch and an advisory
category. Batches retain the exact selected continuation and report applied patch
IDs rather than requiring agents to infer the winner from the final submitted
trial. Existing profile/focus-specific repair priorities now also account for mean
measured trial duration, bounded to preserve exploration; cached trials do not
train the policy.

Completed-session efficiency cohorts retain model, initial build/work-selection
snapshot and observed function instruction sizes. Ratios include failed work and
separate uncached input, cache reads/writes and output. Missing usage produces an
unknown token average, not zero-cost work. Session-start cohorts can contain
multiple compiler ranges; compare similar workloads and collection windows.
These are helper-time/session averages, not exact per-function token attribution
or subscription bills. No speed or token-saving benchmark is claimed yet.

### Distinguish source identity, retail differences and helper entry context

A source-assisted MIPS/IDO continuation recovered three functions through original record types and
verified retail branches. It also corrected a misleading nearest-neighbour source identity. Call sequence,
field offsets and branch/exit shape are now retained as advisory source-lead support, with the original
symbol/file alternatives and rejected reasons. Exact byte evidence outranks heuristic support; no name is
made canonical by this score. Compiled differences may also come from inlining or optimization, so the
comparison view does not generate automatic retail patches.

Small SDK helpers may receive an incoming float in `f12` alongside an integer or pointer in `a0`.
An exposed ordinary-C signature can shift integer argument slots. Incoming-register diagnostics and
caller excerpts now point workers toward original static-helper context. `mtc1` into `f12` is a definition,
not an incoming float; stack slots are ABI locations rather than an inferred number of C parameters.

Distinct source/header/profile trials retain their mismatch family and elapsed time. Repeated register-only
misses suggest original type/call-context and declaration-lifetime experiments before equivalent rewrites.
Private local family yields can affect preparation ordering after a minimum sample, while exploration and
ordinary judging remain. Synthetic tests verify these controls; they do not claim new game matches.


### Joint prepared profiles and explicit helper signatures

A five-match source-assisted MIPS batch used IDO 5.3 with `-O3` for four functions and
`-O2` for one, all with `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`.
Original typed records and verified per-function profiles were tested as complete combinations.
A particle candidate improved 438 to 200 under typed `-O3` context, then became exact after
material-return signedness and sound helper float-argument declarations were corrected together.
There was no single-change ablation, so neither correction alone is claimed as causal.

An older SDK audio reference lacked timing logic in the better retained draft. A filesystem
reference needed target-verified retail delete/save behavior. Another original source replay stayed
at 300, with `-O3` worsening to 1828; the original `-O2` profile was restored. These results justify
explicit declaration/call/control review and reusable input-scoped failed trials, not automatic
prototype or retail branch replacement.

Private profile search now budgets new probes rather than slicing two already cached choices.
Later preparation passes advance through installed-toolchain flag variants, caching link failures too.
Compile/time caps and source/header/profile review remain; no private probe changes live build flags.
Synthetic tests cover scheduling and advisory diagnostics separately from the manual game matches.

Repeated source/header concatenation also exposed whitespace-heavy preparation overhead. Anchored
preprocessor patterns must use horizontal whitespace, not `\s*` that can rescan thousands of empty
lines. Padded-source regressions retain original source layout. Preparation now explains when its
round ends before compilation rather than silently reporting zero attempts.

### Structured loops and existing record fields under KMC GCC 2.7.2

A 773-instruction N64 function matched with `gcc-2.7.2-kmc` and
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Compiler settings stayed fixed. A private,
fixed-context replay measured two independently generated changes:

- Recovering a single-entry conditional back edge as `do/while`, while retaining
  nested switch exits and existing outgoing gotos, improved score **5950 → 4770**.
  Later manual work could remove explicit caller-save scaffolding after structured
  loop recovery; that combined observation is not a separate automated ablation.
- Computing a signed word directly into an existing same-type record member, removing
  its later scalar-to-member copy and forwarding the member into subsequent arithmetic,
  recovered the final **15 → 0**, with an ordinary linked exact verdict. Of two generated
  member-forwarding candidates, the other stayed at 15. No record or padding was invented.

Earlier experiments with declaration order, `register` and caller-save switches were
mostly neutral; different compiler/CPU options did not recover the match. Source shape
and liveness deserve targeted trials before broad flag searches for register-only misses.

New bounded hypotheses also split one explicitly narrowed definition from a later wide
index lifetime, and sink a zero initialization into the other arm of a definitely assigning
branch. These have synthetic execution and rejection controls; no new game match is
claimed for them. Width changes exclude unsigned-word arithmetic, `sizeof`, escaped locals,
updates and ambiguous control boundaries. Loop recovery rejects external entries and breaks
or continues whose target would change. All proposals use the normal judge.

Random rewrites can reduce score while changing meaning: nesting formerly independent
compound updates changes the values added by outer assignments. Newly introduced multi-update
expressions are filtered, and pre-random-search checkpoints remain separately available in
candidate-quality evidence. They are explicitly quality-unverified; neither this filter nor
similarity proves C equivalence. Exact match acceptance is unchanged.

A save audit also found that an imported best draft could become matched after the initial
publish while its file remained under `best/`. Promotion now publishes `src/` and its manifest
before acknowledging dirty writes, checks for concurrent edits, and retains an edited
counterpart for the next import. Save/no-op/restart and concurrent-edit regressions cover this.

### Reuse verified context without merging compilation units blindly

A source-assisted MIPS batch recovered eight related particle routines at IDO 5.3 `-O3` with
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`. Original records, aggregate return/by-value
calls and helper prototypes were shared recovery context. The app now offers bounded neighbour-header
hypotheses based on shared record names and target calls, using saved exact declaration snapshots.
Each function must independently pass the unchanged judge; adjacent addresses alone prove nothing.

Another constructor remains a miss: its target caller sets `t2`/`t3` for an internal reader instead of
ordinary external C argument registers. MIPS diagnostics now require read-before-write evidence in the
callee and corresponding definitions around the caller’s direct call. This is a same-unit optimization
lead, not a reason to force machine registers or change an ABI automatically.

Source/header concatenation also inflated scratch padding. A direct local body/header API and Settings
form preserve their separation and source layout. Historical exact source/header/profile bundles can be
explicitly recompiled against fresh inputs and published atomically only after current byte/data checks.
Synthetic tests cover these paths separately from the manual game results.

### Storage scope, object definitions and resumable profile checks

A source-assisted live MIPS batch recovered a local cache function at IDO 5.3 `-O3`
with `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul` only after retaining
function-local static declarations. Moving the objects to extern/global context changed
address loads and register reuse. A scheduler entry at `-O2` needed an original wide
buffer-size argument and original timing-object definitions rather than extern-only substitutes.
These are manual byte-verified observations from the preceding batch, not new automated matches.

Extraction now retains file-level `static`; advisory comparisons call out lost local statics,
extra address-high loads for declared externs and adjacent target word loads against explicitly
narrow MIPS prototypes. None moves storage, synthesizes initializers or infers an ABI automatically.
Synthetic controls cover preserved storage, narrow/wide declarations, overwritten and nonadjacent loads.

Saved-draft flag preflight used to cache the first bounded subset as a complete search.
It now caches each completed profile under the full judge-input scope and resumes unvisited
choices on later passes. A controlled fake-judge regression reaches a later `-O3` exact result
with one new probe per round, retains completed work after interruption and resets on source change.
This test demonstrates search coverage, not an additional game match. Exact revalidation and
compiler-setting approval gates remain unchanged.

A subsequent ten-candidate source-assisted live-API batch passed ten byte judgments
(4,424 function bytes): eight at IDO 5.3 `-O3` and the scheduler/model selector at `-O2`, with
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`. The batch is manual evidence;
none of these matches is attributed to the new diagnostics automatically fixing code.

- Two animation readers required the original indexed-block **macro**, forwarding both
  the set and its data pointer before the index. Declaring that API as a two-argument
  function gave incorrect call registers. Original aggregate returns and frame records
  then matched at `-O3`.
- A static-instance initializer needed the original short-float conversion helper and
  the matrix conversion macro's address-of operation, not an invented scalar conversion.
- A particle-system initializer's static-helper version remained at score 860. Explicitly
  expanding the original four initialization statements in its loop reached zero at
  `-O3`; instruction order differed around unrolled-loop stores. This is a tested source
  hypothesis, not a claim that macros always reproduce same-unit inlining.
- A static renderer reached score 660 with byte-array application access. A typed view
  of the same original object, retaining its large field offsets, plus original display-list
  expression order reached zero. IDO had otherwise folded the address into a different
  base symbol. Large object identity can matter even when addresses are arithmetically equal.
- One model-selection switch needed an extra independent return-zero arm confirmed by
  the retail jump table. Archived source was not the final authority for the retail cases.
- Parenthesized `case (...)` labels exposed a source-import bug: a keyword was treated as
  a private helper name. The importer now excludes `case`, with a regression test. The
  installed app was not restarted; a local macro workaround kept the live trial moving.

### Named SDK source can be useful without a matching machine fingerprint

A private N64 replay used `guS2DInitBg` reported matched in an imported repository as a same-name
source lead for a ROM project. Under the destination's unchanged KMC GCC 2.7.2 profile
(`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`), the automatic pass improved score **2545 → 1160**.
It remains non-matching. Fourteen destination trials and two preprocessing probes were used;
the bounded local repairs did not improve the best donor version further. This is measured
partial progress, not an additional exact match or a claim that names prove SDK identity.

The donor default branch scored 2245. A source-declared `BUILD_VERSION >= VERSION_J` boundary,
with `VERSION_J` explicitly defined as 7 by the donor headers, scored 1160. Its override is
supplied before both headers and body; mixed header/body revision guesses are not intended.
The destination preprocessor resolves compiler conditionals and macros. Its required declaration
closure is about 1 KB rather than retaining the roughly 1.3 MB expanded include tree.

Source/default-width, layout and version evidence can therefore bootstrap a better candidate
before token-consuming work even across project modes. Destination compiler settings stay fixed;
unknown globals/helper definitions and unsupported preprocessing remain explicit limitations.
A repeated unchanged pass used zero additional preprocessing or destination compiles after
input-scoped caching. Synthetic tests cover donor modes, failed-trial renewal, cancellation,
claims, donor rotation, include bounds and exact destination acceptance separately from this replay.


### Turn source-context findings into bounded app hypotheses

The preceding ten-function live batch motivates shared initializer-helper expansion and preservation of
reached macros from an explicitly mapped include tree. Expansion accepts only a candidate-owned static
void helper with one pointer argument and straight-line field assignments. Side-effecting arguments,
control flow, unknown macro expressions and volatile declarations are excluded. Synthetic positive and
negative tests cover this candidate generation separately from the earlier manual matches.

Typed-object notes now flag changed symbolic high bases near raw byte access. Bounded MIPS retail table
reads recognize address-based labels as well as jump-table labels, and surface missing nonmodal arms or
different entry points for grouped source cases. A modal address is only a default hypothesis. Neither
diagnostic invents a record layout or case body. Historical exact bundles can be explicitly batch-approved;
each still requires fresh compilation, original ROM-data verification and the unchanged atomic publish gate.

A further ten-candidate live-API pass recovered seven functions (2,756 bytes): four at
IDO 5.3 `-O3`, three at `-O2`, with `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`.
The unsigned original cheat-mask type recovered a menu initializer that stayed at score 1520
with a signed substitute. An animation callback needed the original same-unit predicate body
around its animation-type call, not a direct Boolean declaration of that callee; this restored
both the range checks and the compiler's inline stack context. The earlier typed-object approach
also recovered save-menu and boss-event routines, with retail behavior checked against the target.
These were manual live-app results, not matches generated by the new repair family.

Negative evidence: preserving helper line directives and testing debug levels did not remove a
remaining two-load scheduling difference in an allocated-block constructor (score 20), nor the
wall-distance routine's scheduling/base-reuse miss (score 300). Original profiles were restored.
A scheduler loop matches its first 256 target bytes, but the candidate has an additional unreachable
epilogue. The nearby padding entry contains two NOPs followed by the original epilogue; the boundary
and layout remain unresolved. No target extent, candidate-code denominator or byte gate was changed.


## KMC GCC 2.7.2: lifetime, spill and scheduling chain (1 November 2026)

**Evidence:** controlled private matching experiments on a 737-instruction / 2,948-byte function,
then ordinary live-API exact instruction and linked-byte checks and a complete byte-identical
12,582,912-byte standalone image rebuild. Profile remained `gcc-2.7.2-kmc`,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Approximately 4 h 50 min were spent on the original search.
No target/judge edits, inline assembly, register bindings or replacement compiler were used.
Game source, assembly, targets and diagnostic dumps remain private.

| Controlled source change | Observed code generation |
| --- | --- |
| Real scalar face-base pointer under register pressure, replacing an explicit stack field | Natural spill reload used the expected scratch register; an equal-offset explicit field did not |
| Face-base declaration before other long-lived spill candidates | Recovered four target spill offsets |
| Explicit vector pointer for calls, direct aggregate destination retained | Changed weighted references/lifetimes and global allocation without changing the intended copy |
| Split secondary counter/table phases; reuse initial index for later phases | Recovered guard registers and delay-slot moves |
| `s8 one=1` in a byte-fill loop rather than an int constant | Recovered literal/pointer setup order |
| Place index zeroing immediately after count zeroing, before vector-pointer setup | Removed final scheduling difference |

**New automatic regression replay:** the last pre-match candidate was recompiled in an isolated
project copy under the same profile: score **60**, linked exact false. The new
`allocation_initialization_order` generator produced two candidates, one byte-identical to the
recorded successful source. That candidate recompiled to score **0**, exact linked bytes true,
with no link/data problems. This recovers an already known match; it is not a new match or proof
that all preceding manual breakthroughs are automated. Public tests use original synthetic C;
no private game fixture is committed.

**Negative evidence:** 896 legal prefix orderings did not improve an earlier checkpoint. Extra
pointer volatility, wide/complex arithmetic, alternate flags and register keywords did not solve
that allocation plateau. A numerically better candidate enlarged the frame while retaining the
wrong register cycle. These outcomes are scoped to the tested parent/profile, not universal bans.

**App changes:** coherent register-map and displayed scheduling diagnostics, frame/stack clues in
agent briefs, distinct register/spill shapes in the bounded frontier, four-step iterative repair
chains within the same compile quota, mixed scalar/pointer declaration movement, call-pointer
aliases, narrow byte-fill constants, adjacent independent initialization swaps and small combined
bridges. Existing source/header/target/compiler-scoped caches and trial history prevent unchanged
experiments being forgotten; changed parents remain eligible. Shared families reach ROM and
supported imported C projects. The separate iterative autopilot phase remains MIPS ROM only.

**Limits:** automatic families deliberately cover conservative shapes. The final historical source
also contains defined equivalent expressions affecting late optimization; it is a generating-source
hypothesis, not a reconstruction of original spelling. A direct stack field is not evidence of a
compiler spill. Instruction multiset/register/frame diagnostics guide exploration only; they never
change numeric scores or replace the ordinary exact judge.

## KMC count lifetimes, delay slots and a final frame-only miss

A private 814-instruction MIPS routine matched under pinned KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -fno-strength-reduce -msoft-float`. Its final
score-400 parent had two opcode differences. A separate unsigned-halfword count,
qualification of the existing guarded initial read, and a bounded modular-identity
lifetime shape recovered the target NOP, immediate shift and registers. The
32-bit identity temporary left a frame-only score of 22; a signed-short temporary
removed eight unused frame bytes and reached exact linked score 0.

The generator replayed the frozen score-400 parent in five proposals: normalization
scope 400, separate count 560, qualified count read 1115, the unsigned guard bundle
22, and its guarded signed-short variant 0. The dedicated width family also
replayed 22 -> 0 directly. These recover an existing match, not additional live
progress or a general success rate. Volatile qualification alone was worse; the
interacting bundle matters. No calls, data, BSS or extra helper functions were
emitted by the exact candidate. The independent function and complete original
12,582,912-byte cartridge comparison passed for the accepted game source.

The guard is not a general padding recipe. The count is reloaded and halved before
the test, establishing 0..32767. Unsigned add/sub operations and the selected
compiler's signed16 truncation recover that same value. Generated narrow variants
have an integer-layout/conversion compile-time gate; host execution checks cover
skipped phases and later conditional reuse, and an optional pinned-compiler test
checks absence of the guard call and extra data. The generator never invents a
callee: only an existing void(void) declaration can supply the unreachable arm.
Unknown field types, escapes, nonconstant shifts and external loop exits are
rejected. Original count values are restored on phase exit, including when a
later overwrite is conditional. Reads remain in place under their original guards.

Search/UI diagnostics now distinguish opcode, register, stack, branch-target and
operand differences, and count differing rendered instruction positions when
lengths agree. These are advisory, including relocation spelling differences.
The exact judge is unchanged. A numeric winner and a candidate with fewer position
changes can coexist in the bounded frontier; a smaller score alone does not prove
that fewer actual instructions differ.

## Small expression recipes and proven local ranges

Edit minimization now works on lossless C tokens, rather than changed lines. Strings and
comments remain indivisible. It keeps the existing trial/time limits and frozen-output
comparison: removing an edit is compiler-output evidence, not proof of semantic necessity.
Several edits on one line can now be separated without rewriting the rest of the line.

Connected deep-search edges ending in an ordinary exact verdict, accepted exact local
experiment results and exact iterative-search edges, can teach small expression templates. Identifier slots allow existing
local names to differ. Templates require unique lexical context, bind only existing names,
and cannot introduce new names, literals, calls or control-flow statements. They are private
SQLite records, scoped to the existing compiler/build profile. Deep search, agent local
experiments and the optional iterative phase try them within their existing compile budgets.
They do not automatically adopt a source or replace the judge.

Controlled regression on a previously matched large MIPS function, using pinned KMC GCC
2.7.2 (`-G0 -mips3 -mgp32 -mfp32 -O2 -fno-strength-reduce -msoft-float`): token minimization
isolated one retained type edit; replaying its learned template recovered score 22 to exact
zero, also after renaming the affected local. These are recoveries of the same known match,
not new live matches or evidence of a broad success rate. No live source was changed.

`proven_local_width` propagates intervals through straight-line integer assignments,
constant masks, nonnegative shifts and bounded arithmetic. It proposes signed short storage
only when every assignment to an unescaped signed-int local fits, keeping int promotions.
Unknown statements, macros, branches, calls, type-dependent operations, overflow and unknown
values stop the proof. An ABI contract rejects incompatible integer layouts. The shared draft
pipeline reaches ROM and imported projects; ordinary compilation and exact judging remain
mandatory. A synthetic mask/shift regression emits identical `.text` with the pinned KMC
compiler. This is not a measured new game match.

## Learning from successful repairs without spending model tokens

New captures in the desktop app can run a bounded automatic compiler-output ablation: at most four
edit-reversion probes, plus frozen before/after controls and supported GCC diagnostics, with a 20-second
soft deadline. In-flight compiler operations retain their existing caps. Only one automatic analysis runs
at once across projects; captures arriving while busy remain available for manual replay. No source is
adopted, match status changed, or repair marked implemented. The Repair examples analysis toggle controls
future work; `DECOMP_REPAIR_ANALYSIS=off` disables scheduling for a process. Historical cases use manual replay.

The ablator identifies removable captured edits under frozen output, not semantic necessity or the original
source. Native pinned GCC diagnostics compare before/after phase hashes, register counts, lifetime records,
assignments and conflict summaries only after proving `-da` preserved both outputs. Truncated/missing phases
cannot establish a comparison. Pseudo renumbering can change dumps without revealing a cause.

Exact connected repair sequences now use source-free mismatch-shape buckets (category, coherent register
substitution count, frame-gap direction, local-offset differences and scheduling evidence), in addition to
the compiler/build profile. Old category-only recipes remain fallback hints; unverified paths do not train.
The existing adaptive policy retains exploration and incorporates measured trial costs. Metrics expose
matches and improvements per compile plus matches per **summed elapsed trial minute**, with denominators,
profile/focus and sparse-evidence labels. This is not measured CPU time or whole-run wall time; unknown timing
has no time rate. Concurrent trial durations must not be presented as one elapsed run.

Imported projects currently skip frozen standalone replay without a copied build adapter. Other compiler
backends can use ablation when standalone captured inputs are supported, but GCC phase flags are not sent to
non-GCC drivers. These limitations remain explicit rather than fabricating comparable compiler evidence.

Private regression validation of the automatic analysis used the last pre-match 737-instruction pair
under the unchanged KMC `-G0 -mips3 -mgp32 -mfp32 -O2 -g3` profile. The baseline remained score 60.
Token-level ablation identified two captured edits; reverting either failed frozen-output preservation,
so neither was discarded. Both diagnostic builds preserved their ordinary output. Captured register
assignments stayed unchanged, while lifetime and scheduling dumps differed. This supports investigating
initialization order without claiming a newly discovered allocation cause. No source was applied and
this replay does not count as another matched function.

### Shared result pointer, branch-local name pointers, then declaration order

A further private 666-instruction / 2,664-byte MIPS function reached exact linked bytes through
the running app, retaining KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3` throughout.
Its initial score was 5455 (91.8%); the candidate had 632 instructions. Splitting both a result
pointer and a name pointer into eleven branch-local scopes improved score to 2660 (96.0%),
660 instructions. Retaining the shared result pointer while making only name pointers branch-local
reached score 40 (99.9%) with all 666 instructions. Only eight register positions differed.
Swapping two plain local integer declarations recovered the final temporary-register substitution
and exact score 0. The linked-byte and instruction checks were true; no data/address problems,
and export layout was ready. A full image rebuild was not part of this function-level experiment.

Controlled failures: splitting only the result pointer and keeping the name pointer shared worsened
the candidate to score 12530 / 597 instructions. Explicit returns versus fallthrough after the
branch-local blocks made no difference to the tested score. Moving the final two temporaries'
load order, or adding `register` to either local, retained score 40 rather than fixing it.

Interpretation: splitting every local is not a general allocator repair. Selectively combining
shared and branch-local lifetimes can prevent unwanted tail merging and restore register allocation.
A mixed-lifetime candidate should survive alongside the all-shared and all-split alternatives;
then bounded declaration-order repairs can finish it. These are generating-source hypotheses,
not proof of original variable spelling or source layout. This result is manual live-API matching,
not a claim that the automatic repair families found all intermediate steps.
### Automating selective branch pointer lifetimes

The shared `branch_pointer_lifetimes` family now tries individual pointer splits and bounded combinations,
retaining the all-shared parent in the existing frontier. A conservative CFG-backed transform can first
copy a void terminal suffix into terminal switch leaves: no fallthrough, default required, all incoming
suffix paths covered, no loops/gotos, and no nested declarations that could shadow copied names. Pointer
reads must be definitely initialized, storage unescaped, scopes disjoint and volatile/C++ shapes excluded.
Simple statement macros can be expanded with bounded side-effect-free arguments; strings, control macros
and opaque captures are left alone. No calls/evaluations are invented or removed by scope changes.

`branch_pointer_declarations` interleaves same-type uninitialized declaration swaps across the retained
mixed-lifetime hypotheses. The cap is 66 proposals total, shared across parents; ordinary trial/time budgets
and source/build caches remain in force. Existing numeric/structural frontier retention and input-scoped
negative trial history handle plateaus and repeated failures. A shorter stream with fewer repeated calls
is now an advisory hint to inspect tail merging **or missing logic**, not an equivalence or identity verdict.

Frozen private replay under the original KMC profile recovered the recorded successful 666-instruction
compiler output. The three lifetime candidates scored 12530 (only result pointer split), 40 (only name
pointer split) and 2660 (both split). The 17th generated proposal, including declaration order, reproduced
exact function fingerprint and all allocated code/data/relocation output of the previously verified control.
Four of 66 proposals recovered that control in the complete regression run. This is an existing-match replay,
not four new matches and not a success-rate estimate. No live source or game data was changed or published.


### Recover predicates and investigate structural misses first

The prior manual source-assisted batch recovered a callback at IDO 5.3 `-O3` with
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul` by retaining the original predicate's
callee-result range check and inline context. A Boolean prototype of the underlying callee had
lost the checks. The app can now propose private actual one-callee range-predicate bodies from
mapped source, with their real declarations/constants; it never invents a helper implementation.
Bounded same-width mask signedness trials address the separately observed unsigned-context match.
Synthetic positive/negative tests cover generation and imported-build exclusions, not new game matches.

Two inverted loads get focused repair ranking. A candidate-only restore/return suffix receives an
advisory to inspect original adjacent padding, not a shortened judgment. The previous scheduler
observation remains unresolved: its nearby padding includes two NOPs followed by the epilogue.
Concrete source comparisons and compiled immediate differences are now shown as revision leads.
Unsuccessful preparations are summarized only under their original source/build scope, so changed
inputs remain eligible and old failed experiments are not mistaken for universal impossibility.

### Selector staging, control flow and debug profiles

A private 741-instruction V-Rally regression recovered an already known exact match using
bounded single-case switch, shared flag-guard join, selector/address staging and unsigned
halfword hypotheses. Selector staging requires the old pointer to be dead on every
successor; qualified variants change an existing load rather than adding reads. Macro
bodies stay in place, and unknown preprocessing/control transfers are rejected. These
are compiler hypotheses, not semantic proofs; the ordinary exact judge remains mandatory.

The original saved candidate scored 1976. The first generated bundle scored 1216 under
its original GCC debug profile. Normal profile preflight then tested the current and
no-debug settings and adopted the exact result through ordinary revalidation. In an
isolated ROM engine/database this used seven compiler invocations, about 1.66 compiler
seconds and 2.33 elapsed seconds, excluding database preparation, repository synchronization
and full cartridge rebuilding. This is known-match recovery, not a new match or a general
success-rate measurement. Source and ROM inputs remain private.

Promising candidates (score at most ten points per instruction) now prioritize installed
debug-flag variants within the existing profile budget. Other candidates retain the
existing optimization-first search. Imported builds still own their compiler flags.
Register-only differences produce compact allocation/address guidance, not a claimed cause.
Revoking a manual MCP connection drains its in-flight attempts, retains their results and
releases only its claims; queued work on that revoked connection cannot reclaim them.

### Measure combined-search effort rather than assume a speedup

The selective branch-lifetime case motivates combining source repairs and preserving correct
regions, but not a universal declaration or formatting rule. The shared frontier now ranks
structural alternatives by newly damaged previously correct target blocks while retaining the
numeric winner. This is advisory and cannot reject an otherwise exact candidate.

An unchanged-profile private 666-instruction KMC GCC frozen-output benchmark recovered the
previously verified control with all deterministic families in 67 candidate compiles (about
20 seconds), including three compiler failures. Restricting generation to the branch families
recovered it in 19 compiles (about five seconds). Both additionally compiled two controls.
The earlier direct branch-family list recovered at proposal 17: these measurements do not
establish a speedup. The round-robin beam and direct list have different proposal orders.
The successful family already generates a bundled lifetime/declaration edit, so its one-family
path is not proof that a multi-edge chain was necessary in this replay. Synthetic plateau and
structure-gain tests separately exercise actual multi-edge continuation, cache reuse and resume.

Keep successful and failed frozen cases as private benchmark inputs, compare first recovery
trial and wall time under fixed context, and preserve data/relocation checks. Do not train exact
repair recipes or adopt game progress from a frozen-output replay alone.

### Unresolved near-miss cohort and search throughput

A frozen private N64 GCC cohort selected eight open functions (at least 20 instructions),
ordered by lowest saved score per instruction. Saved scores were 10, 15, 15, 33, 30, 15,
10 and 25. Each received 16 candidate trials and a 20-second soft deadline, using its
actual stored compiler profile/header and the ordinary linked-byte judge in a copied
build root. Source, symbols, ROM and database stayed outside the repository.

The initial pass used 128 candidate compiles plus eight baseline compiles, approximately
32.04 elapsed trial seconds / 27.86 compiler seconds. Reserving bounded plateau-combination
probes then used the same compile count, approximately 29.44 elapsed trial seconds /
24.31 compiler seconds. Neither pass found an exact match or a lower score. These small
sequential timings are not evidence of a speedup; there is no demonstrated matching gain.
The harness omits richer donor/address pools and automatic profile search, so this measures
the shared targeted-source beam, not all autopilot capabilities. Baselines remained unresolved.

The shared beam now reserves one immediate combination probe for each of two distinct
flat-score families, retaining the existing wave. Optional frozen benchmarks can collect
several exact alternatives, retaining intermediate parents even when the last wave item
is exact; live autopilot still stops at its first verified exact result. Numeric and
structural alternatives remain bounded by the existing width/depth and attempt budgets.

Iterative trial caching now includes the full candidate/build scope (header, target,
relocations, symbols, compiler flags and referenced helper evidence), excluding old partial
cache keys. Unscored reports are audited but not persistently cached because resource/tool
failures are not reliably distinguished from syntax errors. Measured uncached iterative
trials train the existing adaptive policy; exact-zero reports require an explicit exact
verdict there. Smoothed exact throughput influences priorities within the same profile and
mismatch kind while original queue interleaving preserves exploration. Durations are summed
trial elapsed time, not CPU time or whole-run wall time.




### CPU recovery from source context and measured retail differences

A source-assisted eight-match batch under provisional IDO 5.3 with `-mips2 -G 0 -non_shared
-Xcpluscomm -Wab,-r4300_mul` required original aggregate/prototype context, preserving an actual
cache-access call, a four-byte application-record gap, retail switch labels and an 18-character
limit in place of 16. Five used `-O3`; three menu/state routines used `-O2`. Those are manual
recoveries, not evidence that the full automatic pipeline already reproduces all eight.

Private CPU replay of two pre-match inputs at `-O2` used the ordinary sandboxed compiler and ROM
link/data judge. The layout baseline scored **40**, with four mapped stores consistently shifted by
four bytes. A generated private gap before the earliest affected field recovered linked-byte/data
exactness. The constant baseline scored **50**; a generated coherent 16-to-18 macro trial recovered
linked-byte/data exactness. These replay known matches; no new function or coverage is claimed.
A separate controlled switch replay retained already-mapped retail data bindings, removed the grouped
error arm and restored older case numbers. Table-based relabelling plus an agreeing structural arm clone
also recovered linked-byte/data exactness. Arbitrary missing arm bodies or unknown bindings remain
unsupported. The source and ROM remain private; no live project or repository was changed by these replays.

Mapped source trials now preserve actual differing ANSI prototypes, with actual source types rather
than guessed scalar/aggregate replacements. A target call can disable a conflicting header macro
while retaining the actual declaration. Named records from exact neighbours are transferred privately
with their record dependency closure; incompatible scalar aliases reject that merge. Layout probes
require at least two mapped field operands with the same small positive displacement; store mappings
use the assignment's left side, not a similarly named saved-state record on the right. Constant trials
require aligned instruction evidence tied to a comparison or explicit sizeof(array) bound. Switch
clones need identical target instruction structure, ABI registers/constants and a bijective data-symbol
mapping. These hypotheses retain the ordinary judge; they never establish missing source semantics.

The movement near misses at scores 51/60 remain unresolved. Actual pointer-ABI corrections, original
helpers/constants, aggregate copies and line directives did not match; one flattened ABI rewrite
scored 1011. Reuse focused declaration/stack/helper families and cache failures by exact source,
header, target and build state instead of repeating those experiments across agents.

## CPU trials from typed-source and retail-control recoveries (2 November 2026)

Seven source-assisted recoveries at provisional IDO 5.3 `-O2 -mips2 -G 0 -non_shared
-Xcpluscomm -Wab,-r4300_mul` motivated bounded CPU rules. An omitted actual float-argument
prototype caused default double promotions, extra conversions and a larger frame; restoring
that declaration matched. Two commented-out calls present in retail recovered menu input.
A timer's repeated load/store differed by four bytes; a private adjacent-word field-order
hypothesis matched without shifting the remainder of the record. Retail error tables required
real case groups and message-address bindings, not invented strings or initializers.

The CPU pass now tries actual missing prototypes, complete commented target calls with
callee/multiplicity evidence (including simple aliases), isolated word-field order trials,
and paired target data-address operands for declared arrays. Existing table-supported arm
cloning supplies grouped error cases. A narrow MIPS terminal conditional-clear/flag-set
reader can propose an explicit byte-offset body only when its instructions are missing from
the measured candidate, argument origins are traced and its branch exits to the epilogue.
Arbitrary missing branch bodies are still a review task. All proposals use ordinary compilation,
linked-byte and data checks; none establishes a field's semantic name. CPU queues remain
bounded, cache fingerprints advance to v7, and improvements feed subsequent measured trials.

**Evidence level:** private replays of known recoveries are controlled regression evidence,
not new matches or a claim of general effectiveness. SDK sequence setup and a static tempo
helper still differ under isolated compilation; possible private entry/call clobber ABIs are
reported for translation-unit investigation. A register/frame-only mismatch gets a separate
scheduling hint. No wrapper, target-boundary alteration or assembly bypass was introduced.

Four private linked-byte/data replays reproduced known matches: commented calls (baseline score
4088), missing float prototype (3024), timer field order (20), and the measured terminal branch
(1680), each to exact. The terminal-branch replay uses explicit byte offsets, not inferred field
names. These tests leave the live project untouched and keep game source/evidence outside Git.

## Preserve source context before random trials

IDO 5.3 with `-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`:

- At `-O3`, an actual static sorting helper with its original physical line directive recovered an exact function;
  including the same helper at a different line still scored 660. Automatic preparation now tries bounded
  same-file static definitions, retaining bodies and physical lines, without inferring implementations.
- A mismapped pair of callback addresses left score 40 at `-O3`; verified callback identities recovered exact
  code. Automatic hypotheses require two independent exact-neighbour contexts to agree and the replacement
  symbol to appear in target operands. Consensus is a hypothesis, never identity proof.
- At `-O2`, a real include prototype with a float argument removed implicit double promotion (score 650 to 10);
  the correct function binding then recovered exact code. Preserve pointer returns and aggregate parameters too.
  Preparation tries include declarations even when an explicit flattened header already has a conflicting ABI.
- A disabled original boolean configuration hid a cinematic path. Private boolean alternatives are bounded to
  switches actually defined in the mapped file and must reveal extra calls; explicit user overrides are retained.
- At `-O2`, exact code with separated constant pools failed standalone export. Referencing all existing constants
  changed scheduling (score 330); retaining one generated pool recovered exact code and a complete original
  cartridge rebuild. Scalar repair uses labelled target floating loads, unique ROM-value matches, bounded numeric
  arithmetic, and all/keep-one alternatives. Ambiguous equal-value addresses require human review.

Evidence: the above are manually recovered original-source cases. The new automatic generators have synthetic
regression coverage; their presence does not establish that they reproduce every original case. Export repair
claims each exact function, recompiles and checks linked bytes, data and export layout, then publishes through
normal attempt recording with a stale-input guard. It preserves coverage/readability and still requires a full
repository rebuild. Unchanged failed trials are cached. Private context and export repair are ROM-only; imported
builds retain their own headers and need an adapter. Target scalar-load reading currently supports MIPS;
other processors need a connector reader. No game data belongs in regression fixtures.

A controlled compiler replay also recovered the historical sort function automatically with `-O3`: removing
its private helper and restoring the original definition through the new generator passes linked-byte/data
verification. A first replay imported a same-named global helper that was shadowed by a callback parameter;
that failed. Helper discovery now excludes function-pointer parameters, retains their actual declarators in
forward declarations, and has a synthetic regression for the shadowing case. This replay adds no coverage.

An additional original-source audio-frame function was recovered after restoring the entry's physical line
and an already-present static helper's original line. Entry line alone retained score 270; restoring the
identical helper's line recovered exact linked code/data at `-O3`. Preparation now also generates that private
line-only variant when the existing and reference helper definitions are token-identical. A controlled replay
reproduces this result. Differing helper bodies are not replaced by this rule.

A full original-image build then caught an unused tentative object in a flattened private header:
IDO emitted raw `SHN_XINDEX` without an `SHT_SYMTAB_SHNDX` table. Isolated function bytes/data still matched,
but GNU ld rejected the malformed symbol table. Restoring the actual include header's `extern` declaration
preserves the function bytes without inventing storage or changing ELF indices. Export diagnostics now block
this malformed case before advertising layout readiness; a homemade ELF regression retains the exact-function
verdict separately. A complete repository build remains necessary.

## KMC GCC 2.7.2: one-shot scopes and equivalent Y lifetimes (2 November 2026)

**Verified case:** a 727-instruction / 2,908-byte rendering function, `func_80073B64`.
Profile remained `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, big-endian
32-bit MIPS. The normal running-app judge accepted score **0** and exact linked bytes;
its saved source SHA-256 is `04513bf5a694f8ce363a557c3e75008171b456f6694368a30517175bf9734a1e`.
The standalone function check independently accepted the same source and isolated header.
The complete 12,582,912-byte cartridge also rebuilt identically, SHA1
`da3f262dfb86cff1376c9a1aa081212e5a7aad64`, with all earlier selected C retained.
[Reconstructed source](https://github.com/RetroReversing/v-rally-decomp/blob/26fd785dad539c058cb1a93a05f4f0b0161d0b8b/src/code_8004BEB4/func_80073B64.c)
and [portable verification evidence](https://github.com/RetroReversing/v-rally-decomp/tree/26fd785dad539c058cb1a93a05f4f0b0161d0b8b/progress)
are retained together. These are reconstruction choices, not proof of the original source spelling.

### Instruction identity can conceal a register-priority problem

At score 275, all 727 ordered instruction mnemonics agreed, but 23 displayed positions
had register differences. The compiler's actual `lreg`/`greg` dumps showed a projected
32-bit index being allocated before X. A source variable's 16-bit and 32-bit views did
not necessarily share a pseudo register. Widening the whole variable or separating its
earlier half-width phase changed already-correct code and did not solve the miss.

Three nested, **single-execution** `do { ... } while (0)` scopes around the existing
Y restore and X advance changed allocation priority. No new calls or iterations were
introduced. In the pinned compiler's dumps, X's weighted reference count changed from
16 over 73 live instructions to 22 over 74, while the projected index remained 14 over
38. X was then allocated before the index, recovering the required register choices.

The public [GCC 2.7.2.3 reference source](https://ftp.gnu.org/gnu/gcc/gcc-2.7.2.3.tar.gz)
explains a mechanism: `flow.c` weights references by recorded loop depth, and
`global.c` orders allocation using reference count, its integer logarithm, and live
length. The actual pinned KMC dumps retained loop notes for these one-shot scopes.
The reference archive is supporting context, not a claim that the modified KMC binary
is identical to that later GCC release. No reference compiler was built or used to judge C.

| Frozen-profile experiment | Normal judge result | Interpretation |
| --- | --- | --- |
| One-shot scopes around only the X advance, depths 1/2 | 740 | Worse; scope placement matters. |
| Same location, depths 3/4/5 | 505 | Register choices largely recovered, but scheduling and an extra argument copy remained. |
| Include the adjacent Y restore in the scope, depths 3/4 | 390 | Restored instruction order; one redundant copy remained before a conditional call. |
| Same scoped source; restore Y after the second call from the existing saved Y plus one instead of incrementing the current Y | **0, linked exact** | Preserved the older equal-value temporary long enough to avoid the copy. |

The useful score-390 alternate was more than 25% worse than the saved score of 275.
A scalar-score frontier alone could discard it. Keep such evidence in a separately bounded
experiment; do not silently replace the saved best or raise every search budget.

The last change is safe in this case because the saved value is the same pre-decremented
coordinate, both restoration expressions produce the same 16-bit value on every path,
and the local coordinate's address never escapes. The intermediate dump placed a copy
before a bounds branch and its consuming shift inside the branch. GCC's CSE reference
implementation prefers a longer-lived equal-value pseudo; extending the existing value's
use after the call recovered the direct shift operand. This does not justify replacing
arbitrary increments with an earlier value.

### Bounds for future automation

- Treat one-shot scope placement/depth as a bounded, compiler-specific hypothesis. A
  lower instruction similarity score remains the saved best until a normal exact verdict.
- Reject regions with `break`, `continue`, labels or incoming jumps whose binding or
  reachability would change. Preserve declaration and object lifetimes, volatile accesses,
  evaluation count and call order. Nested wrappers must still execute the body once.
- Compare output and intermediate phases, not pseudo numbers alone. A successful
  allocation change can introduce an extra copy or change delay-slot scheduling.
- Global coordinate widening, union views, removing the second decrement/restore pair,
  simple mask removal and explicit copy/cast variants all regressed this case. Do not
  repeat them blindly on the same inputs.
- Diagnostic `-da` builds informed the investigation. Acceptance used the ordinary
  profile without `-da`; symbolic relocation annotations did not override exact linked bytes.
## Measured layout, narrowing and declaration recovery

For provisional IDO 5.3 with `-O3 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`,
controlled private replays recover two historical original-source matches without model calls:

- The saved inline constructor scored 20. A bounded join of adjacent assignment lines in its called private
  helper recovers exact linked bytes/data. Other joins miss; this is a compiler scheduling hypothesis,
  not a claim that reflowing arbitrary source is helpful. Only small instruction-multiset-preserving
  differences trigger the layout family; comments, directives and explicit line macros are retained.
- A large gameplay routine with an extra unsigned-halfword normalization scored 1200 after actual verified
  call/declaration context was supplied. Removing the corresponding explicit narrowing cast recovers exact
  linked bytes/data. Candidate-only masks/shift pairs gate width-specific trials; types are not inferred
  from a score. A bundled alternative covers two relevant casts, still through the ordinary judge.

Private context recovery can replace tentative objects with compatible actual `extern` declarations from
reached authorized reference includes, and add missing callee aliases only with independent exact-context
agreement plus target-symbol evidence. Aliases precede real prototypes to retain float/aggregate ABI.
Existing private inline bodies may recover original line numbers from token-identical non-static source
without importing a new global definition. No shared header is edited by these hypotheses.

Measured mixed integer/float entry and retained caller-saved argument patterns are recorded separately.
Ordinary signature families are deferred behind context/body trials; no register binding, boundary or ABI
adapter is synthesized. Full translation-unit/clobber adapters remain planned. Generic layout/extern logic
reaches applicable source preparation; private header publication remains ROM-only. Extra-normalization and
private-entry readers currently cover MIPS; other processor readers are planned. Controlled replays are not
new coverage and do not guarantee that every archived source difference is automatically recoverable.

### Bounded pointer-call and mixed-initializer allocation repairs

A previously verified 739-instruction menu formatter used KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Reusing a heading total, separating a later
integer/address phase, and using equivalent formatter-call branches changed allocation before
GCC merged the identical branch bodies. The final difference was the order of two independent
instructions. Ordering four local initializers recovered it. These are reconstruction choices,
not evidence of the original source spelling. The game match and complete cartridge build were
verified separately before these app changes.

Shared generators now offer CFG-dominated, single-definition pointer guards, including
pointer-to-array locals and two-level identical-call branches. They do not read buffer contents.
The initializer family can extract independent comma resets from a call, group three or four
mixed pointer/scalar initializers, and delay a private constant address only when the reached call
dominates all pointer uses. Scope boundaries, pointer-storage escape, hidden macro access,
unknown control flow and alternative reaching definitions prevent these transformations.
A paired family combines a proven integer value-phase split with a disjoint-lifetime merge.

Search still uses its existing compile/time allowance. A measured regional recovery with no
instruction-count growth can occupy one iterative frontier slot even beyond the ordinary 1.25x
score ceiling, capped at 8x. Numeric and regional alternatives stay separate; no search score
or displayed-row classification can accept C. ROM linked-byte/data checks and project objdiff
remain the only match verdicts.

Private controlled replays against the original ROM recovered the final initializer checkpoint
(score 160) in 17 candidate compiles, about 17 seconds. A preceding pointer-allocation checkpoint
(score 725) recovered exact output in 108 candidate compiles, about 101 seconds, through
`initialization_cluster`, `balanced_pointer_calls`, and `allocation_initialization_order`.
These recover an existing match, not additional game progress. A broader 520 checkpoint did not
improve in an initial 128-trial run or a targeted 218-trial, four-stage run. Full rediscovery from
that earlier shape is not demonstrated. Inputs and captured game code remain private.
## Bounded context stages before source permutation — 2 November 2026

A source-assisted MIPS ROM batch demonstrated three distinct requirements: an actual small animation wrapper compiled inline, an actual range predicate plus the original local declaration order, and exact source whose separated string/scalar pools blocked export. Controlled private IDO 5.3 replays with retained `-O3`/`-O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul` profiles reproduce the historical wrapper match and the predicate/order match through generated candidates. Actual verified call aliases are inputs; the pass does not infer an address from a function name. These replays add no coverage.

Declaration recovery requires the complete uninitialized local name/type set to agree; initializers, directives and changed types are not reordered. A wrapper absent from retail's out-of-line calls is copied from the authorized reference, with its physical line, into private context. Ordinary same-unit callee-body context is a separate bounded hypothesis, not an invented calling convention. An early live temporary across a call, without a spill/reload, demotes ordinary ABI guesses.

Export candidates may reference a complete ROM-proven string and floating arithmetic macros while retaining one generated pool. In the private example, retaining the 0.01 literal while referencing the other two doubles and the actual diagnostic string preserves exact code; replacing that scheduling-sensitive literal fails. Publication still requires exact linked bytes/data, fresh source/context/claim guards and the normal standalone rebuild. Prefix normalization is provisional until that build passes.

MIPS pointer-lifetime candidates require an entry field load saved to the stack, restoration around a specific call occurrence and a subsequent field clear, plus the candidate's same-offset clear and unique typed pointer member. Explicit commented condition recovery and repeated neighboring-array-element repair are bounded source hypotheses. Entirely absent case/enum reconstruction remains planned; no generic CFG synthesizer is claimed.

A third controlled replay starts with the historical draw candidate at score 4,069. Generated saved-pointer lifetime C reaches 40; generated neighboring-float-element correction then links exactly. The lifetime reader selects the second call occurrence, not the earlier same-callee call. Restricting candidate cleared fields to the straight-line entry region avoids confusing later cleanup clears with the saved pointer. This is another historical replay, not new coverage.

### Bounded follow-up automation

The shared repair pipeline now exposes `loop_weight_scope` and `equal_value_increment`.
The former wraps up to three eligible scalar updates (and adjacent pairs) in one or
three one-shot scopes. The latter preserves an earlier equal local through an adjacent
increment. It requires identical declared integer types and no intervening statement;
this deliberately does not infer equality across a call or branch. Qualified, escaped,
macro-backed and assembly cases are excluded. At most twelve proposals are generated.
Register-only evidence prioritizes these families; all adoption uses the normal judge.
These are shared source hypotheses, available to ROM and imported-project candidates,
with legacy GCC as the motivating compiler, not a guarantee of benefit on other compilers.

The existing six-source frontier already retains distinct allocation alternatives even
when their numeric score exceeds the saved best by 25%; a regression covers 275 versus
390. Existing durable local-trial scopes include build identity and candidate hashes;
new family IDs participate in the same cache, budget and fairness rules. No new automatic
diagnostic compilations or higher default budgets are introduced.

Optional GCC dump summaries now report loop-begin notes and the legacy allocation-weight
heuristic alongside raw reference/lifetime counts. Pseudo numbers are not stable source
identifiers. These are advisory comparisons, only valid when diagnostic output is unchanged.
Synthetic host-compiler execution tests verify the narrow transformations; they do not
establish a fresh retail match or prove the full original cross-branch repair is automated.

### Target-block search is guidance, not a compiler diagnosis

A linked MIPS reconstruction using KMC GCC 2.7.2 (`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`)
was replayed read-only from the same unresolved score-520 source and frozen header.
Interleaved first-block/whole-function rankings and bounded regional combinations
still returned score 520 after 128 candidate compiles (about 71 seconds). This is
negative evidence: ranking existing repairs did not recover the original match.
Do not repeat it as a demonstrated end-to-end speedup. Single/pair captured-edit
subsets are controlled output experiments, not proof that the original compiler used
that source shape. No game source or binary is included in the regression tests.

The later allocator checkpoint still recovered the normal linked/data exact result
through initializer extraction, balanced pointer calls and initializer ordering:
110 candidate compiles, about 100 seconds (previous implementation: 108 compiles,
about 101 seconds). These are recovery regressions for an already known match,
not new game progress; wall-time variation is not evidence of lower search cost.

A subsequent source-assisted map routine required a retail cinema guard absent from the reference. Reusing a supplied draft's complete leading guard under a measured missing-call constraint reduced a controlled prepared candidate from 5,065 to 1,478; raw draft memory macros still differed from the exact typed reconstruction. This CPU rule is an improvement lead, not an automatic exact-match claim. Same-unit object-definition evidence separately recovered a boss initializer: supplying the actual object definition instead of an extern removed redundant address-high loads. Do not turn unrelated externs into definitions without actual source evidence.
## 2 November: paired operands and a derived loop counter (KMC GCC 2.7.2)

A controlled private replay of a 619-instruction gameplay routine used unchanged KMC GCC 2.7.2
flags `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The historical exact source was already verified;
these measurements are rediscovery, not new game coverage or proof of original source spelling.

- **160 → 60:** stage both signed-halfword subtraction operands through the same existing,
  dead `int` pair across multiple stores. For halfword destinations, unsigned-halfword loads
  preserve the low 16 bits; full-width destinations must retain signed loads and their full
  difference. Fresh temporaries and casts alone did not recover the same allocation.
- **60 → exact:** remove an independently initialized/incremented loop counter and derive it
  from the unit-step index (`stride = index * 5`) before its first read. Combine that change
  with removal of **one** identical-arm scalar guard. Counter reshaping alone scored 135;
  removing the other guard scored 155. Removing every guard would lose the needed source shape.
- At score 60, instruction content and registers agreed; one zero-store initialization moved
  relative to three setup instructions. GCC diagnostic dumps implicated induction creation and
  scheduling priority. This supports a source-shape hypothesis, not an arbitrary instruction swap.

### Automatic rediscovery and limits

The shared candidate pipeline now offers `paired_operand_reuse`, `affine_counter`, and
`affine_counter_guard` to applicable ROM and object-project consumers. Scalar reuse uses CFG
liveness, including backedges and gotos; it rejects escaped/shadowed/live scratch values and
side-effectful or unsupported operands. The affine rule supports a deliberately narrow form:
zero-based unit-step index, positive literal bound/stride, an unescaped word counter, no body
updates to either induction variable, no live-out counter, no incoming jump into the loop,
and an insertion dominating all counter reads. An internal skip goto is allowed when proved safe.
One guard is removed per bundled candidate; conditions must be initialized ordinary scalar locals.
There is no exhaustive C equivalence proof; the ordinary linked-byte/data or objdiff judge remains
mandatory. No flags, machine registers, source headers or live project files are changed.

A frozen database snapshot, fixed context/target/ROM and installed compiler were used for both
main (`69f7a77`) and the new code. Equal 48-candidate limits produced:

| Saved checkpoint | Main | New targeted search |
| --- | --- | --- |
| Score 160 | Best 160 after 48 trials, 29.6 s | Exact on trial 43, 33.2 s |
| Score 60 | Best 60 after 48 trials, 29.1 s | Exact on trial 1, 3.2 s |

Times include candidate generation and judging; the baseline compile is additional. A direct
family-only replay first reached 60 on paired candidate 14 (~5.6 s), and exact on the third
counter/guard candidate (~1.25 s). Integrated search can reach exact through the edits in the
opposite order. No model calls or live adoption were used. Timing is local evidence, not a
cross-machine performance guarantee or an end-to-end comparison with the historical investigation.

Ranking distinguishes pure scheduling from register-normalized operation movement. Identical
compiled code/relocation chains receive lower frontier priority, while two source alternatives
per output/depth remain eligible: a later edit can distinguish initially identical outputs.
The existing full-input failed-trial cache avoids repeating already judged source candidates.
No first compile can be skipped based on source appearance, displayed rows are not an exact
verdict, and output-neutral chains are not unconditionally discarded. Targeted search reserves
four of its twenty proposal slots for smaller applicable families when one family is large.

## Recent repair-example lifetime hypotheses — 2 November 2026

Six private captured cases were replayed with their frozen successful compiler/header settings and
installed compiler, without editing live projects or adopting C. Five new shared source families cover:

- Merging matching leading sibling-arm locals while keeping initializer execution in each arm. Both
  isolated wrapper scopes and declaration-only parent scopes are offered; sibling declaration order
  and grouped declarations are bounded alternatives only for independent initializer expressions.
- Retaining a narrow index in a word local at exit-relevant loop tests and using it in the common tail.
  CFG traversal must prove a selected test follows every last index write on every tail-use path.
  Intermediate loops overwritten before the tail are excluded. An optional switch-tail goto cleanup
  requires a sole jump to the label immediately following its enclosing switch.
- Staging known float arithmetic in typed locals before the call's integer casts. Only pure float-local
  expressions and float literals qualify. Immediately preceding dead, unescaped float copies can be
  folded into the staged expression; arithmetic order and parentheses are retained.
- Inlining a private word temporary's load-plus-sum chain into two uses in one pure RHS. Explicit
  word conversions preserve temporary-width boundaries; calls, volatility, escaped storage, mutation
  and later temporary uses reject the hypothesis.
- Moving an unescaped local pointer assignment past a plain error return, including a small grouped
  version for repeated occurrences. The replacement source is a scoped local pointer or array; globals,
  unknown storage, braced/compound return guards and escaping pointer storage are excluded.

The existing ROM donor-address proposer already requires aligned agreeing HI/LO pairs. Complete
candidate-local mappings now retain a slot ahead of raw-address guesses in its bounded proposal list;
comments, strings and unrelated identifier tokens remain untouched.

Frozen compiler-output regression scores against each successful control were 15→0 (sibling locals),
355→0 (loop exits), 60→0 (float staging), 5→0 (scalar chain), 185→0 (pointer update), and 40→0 (donor globals).
A winning generated output reproduced the control's normalized instruction/relocation fingerprint and
allocated constant data in each case. These are historical rediscoveries, not new game coverage and not
standalone linked-byte/objdiff verdicts. Other generated alternatives can be neutral or worse. Only the
ordinary judge may accept them during actual searches; no global header, flags, ROM bytes or judge
thresholds are changed. Synthetic host-C checks compare integer/pointer observations and bounded float
inputs at ordinary optimization levels; they are not a proof for arbitrary C, FP environments or inputs.

## Targeted proposal pages and complementary follow-ups

The historical 619-instruction halfword/counter example also exposed a search-selection problem:
fair one-per-family interleaving can still hide a useful later member of one family. On current
main `f399309`, a frozen score-160 checkpoint improved to 100 but exhausted its selected branches
without recovering exact within 96 trials. This differs from the older main used in the preceding
43-trial comparison; historical numbers are not interchangeable baselines.

The shared selector now retains evidence-ranked order even when a candidate already exists in the
ordinary pool. After a measured improvement, paired-operand and affine-counter families can receive
an earlier bounded window; only two follow-ups jump ahead of queued ordinary work. A proposal page
reserves two positions for other available families, and untouched generated proposals retain a
parent/pool cursor. Resuming a build-scoped frontier can therefore reach beyond the old shortlist.
A two-parent generation cache is separate from compiler-verdict caching and includes all generation
inputs. Unknown source/assembly correspondence remains ordinary-order advice, not an inferred map.

The replay adapter must preserve an already ordered `Page`. Interleaving it again erases the very
family weighting being measured. Regression tests cover this, candidate-order retention, reaching
candidate 25 after a restart, cache invalidation, and budget/input checks on comparisons.

Verification uses unchanged KMC GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, copied database/ROM
inputs and ordinary linked-code/data verdicts. The comparison includes five starting checkpoints
across four functions: two historical checkpoints of the same routine, plus three currently unresolved
52-, 124- and 295-instruction routines. All cases receive a maximum of 96 candidate trials and 120 seconds;
the baseline compile is additional and the current compile/generation may finish after the soft deadline.
These are target-specific CPU replays with no model calls or live adoption, not evidence of a measured
reduction in agent tokens or new game coverage. Reports retain unsuccessful cases as well as recoveries.

| Frozen starting checkpoint | Main `f399309` | New selector |
| --- | --- | --- |
| Historical score 160 | Best 100; 96 trials, 60.5 s | Exact; trial 35, 33.0 s |
| Historical score 60 | Exact; trial 1, 4.7 s | Exact; trial 1, 4.4 s |
| Unresolved 52-instruction routine, score 10 | Best 10; 96 trials | Best 10; 96 trials |
| Unresolved 124-instruction routine, score 15 | Best 15; 96 trials | Best 15; 96 trials |
| Unresolved 295-instruction routine, score 15 | Best 15; 96 trials | Best 15; 96 trials |

These measurements were made on feature commit `7337280`, before incorporating the subsequently
merged main changes. Across these five inputs, compiler invocations including baselines fell from 390 to 329 (61 fewer),
with one additional historical recovery and no lost recoveries or best-score regressions. The three
fresh near misses did not improve. Wall time is observational, with other processes possible; the
compile counts and ordinary exact verdicts are the stronger evidence. The recovered example used
existing structural follow-up triggering plus the new complementary ordering/focus; the new numeric-only
burst path is covered by synthetic tests, not credited with this recovery without an ablation.
### A no-return fragment can be an import-boundary error

A private N64 ROM audit found five compiled-function prologues imported as 4–12 byte
assembly targets. The code scanner aligned the last visible return into the following
prologue, but missed a distant return after a long body with no new frame. The complete
functions were 3,472, 3,996, 4,092, 3,836 and 9,024 bytes. Matching frame pops and returns
exist in the original image; 24,376 bytes had been classified as binary data. A fresh
split preserves every existing matched target. These are recovered matching targets,
not new C matches or evidence that any compiler flag changed. Check surrounding original
bytes before declaring a fall-through fragment handwritten assembly.

### CFG equality and scope composition replay

The next follow-up adds `cfg_equal_value_increment`: a must-equal analysis over the
existing local CFG. It intersects reachable predecessors, tracks same-type scalar copies
and modular unsigned +/-1 updates, and invalidates unknown writes. Calls preserve only
unescaped locals. Signed nonzero offsets, address-taken values, uncertain macros,
unsupported syntax and excessive analysis work receive no advice. Literal-zero do-loop
latches are recognized as one-shot; arbitrary loop conditions remain conservative.
Read-only expression macros may be inspected, but hidden writes, calls, control flow,
local aliases and nested macro expansion are excluded.

Scope/value families are complementary in the ordinary paged iterative selector. A
bounded scope detour can exceed the usual 25% scalar ceiling (up to twice its parent
ceiling) when register-only evidence changes, code growth is at most one instruction,
and frame evidence is unchanged. At most three such bursts share the existing depth,
compile and time allowances. Identical measured compiler outputs from two siblings
demote that parent/family behind fresh families, without deleting pending candidates.
The build/rule-scoped frontier includes the new analysis in cache invalidation.

Private frozen-output replays used the installed pinned `gcc-2.7.2-kmc` with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` and the exact saved header. The control was the
previously independently verified successful source, including allocated sections and
relocations. No live project was modified.

| Starting checkpoint (historical linked score) | Replay baseline score | Candidate compiles to control recovery | Time including two control compiles | Winning families |
| --- | --- | --- | --- | --- |
| Later scope checkpoint (390) | 230 | 17 | 21.1 seconds | cfg_equal_value_increment |
| Earlier checkpoint (275) | 115 | 29 | 30.3 seconds | cfg_equal_value_increment, loop_weight_scope |

Replay scores compare compiler objects to the frozen successful output; they are not
the historical linked-byte scores. These are rediscovery regressions, not new game
matches or a general speed guarantee. The same paged selector supplies replay and the
live iterative search; replay reports candidate counts, winning paths and time.

### Context triage before ordinary body permutation

A controlled IDO 5.3 `-O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`
replay showed that an actual same-file object definition removed five redundant address-high
loads and made an initializer exact. Original definitions are already the first source preparation
variant; diagnostics now prioritize the largest measured excess. This is not permission to convert
arbitrary externs to zero definitions.

A supplied retail guard improved a historical source candidate from 5065 to 1478, while its manually
typed equivalent matched. Raw-field macros can change aliasing and register allocation. CPU follow-up
now translates exact supported raw macro bodies through unambiguous existing simple scalar record
layouts before testing the raw guard. It never guesses member names, offsets or new records. Complex
nested layouts and whole-unit private ABI compilation remain gaps, not solved cases.

Source evidence is also classified before repeating broad body trials: disjoint compiled call sets
plus largely different field offsets request source-identity review; C89 `register x = ...` shadowing
an earlier typed local gets an implicit-int warning. Neither warning rejects or edits a candidate.
Measured private temporary-register entry and retained-register ABI cases route toward translation-unit
context, and suppress only generic declaration/stack-shape follow-ups, retaining targeted repairs.
An extra restore/return epilogue receives a separate route; target extents and byte judgment are unchanged.
Cache version 11 includes the same complete candidate, header, settings and destination inputs.

### Current exact callers as prototype hypotheses

A current exact caller's candidate-owned plain scalar prototype can seed another caller
in the same game and compiler profile. Only donors with unchanged recorded exact inputs
and no verification regression are considered. Conflicting declarations in the bounded
donor pool are rejected; macros, static helpers, variadics and uncertain declarations
receive no advice. The recipient's own declaration is changed only in a candidate.
Every recipient must pass the normal judge; shared headers are never changed by this
feature. An exact caller does not establish the callee's original C signature.

Relevant donor evidence/header changes invalidate exhausted recipient frontiers. Matches
unrelated to the referenced callees do not reset those searches. The first-difference
suite in docs/repair-replay.md found no additional recovery in its six-case snapshot;
no compiler-specific success is claimed from that benchmark.

### 2 November: parent-specific ineffective families and small lifetime probes

Compiler: installed pinned KMC GCC 2.7.2, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Three frozen unresolved private gameplay routines had register-only differences: a pointer versus
shifted index, a pointer versus store constant, and a shifted value shared by a branch/call.
The previous 96-trial-per-input replay spent 189/288 trials on unchanged code/relocation output.
That is measured waste in this corpus, not a claim that source shape never matters on a plateau.

Iterative search now reacts after two consecutive unchanged outputs from one family on one exact
parent source. It softly defers queued siblings and retains a deferred exploration slot in three;
no candidate is deleted, and another parent starts fresh. Missing/unscored output is not evidence.
The complete frontier build scope and repair-rule hashes isolate this history from other targets,
headers, settings and rule versions. Two source shapes per identical output/depth remain available.

New bounded source hypotheses stage a positive representable store constant in an ordinary block
local, split identical-type local assignment chains around a simple shifted expression, or commute
pure member/index addition without capturing a larger expression. They are shared draft/agent/local
experiment families; only the existing destination judge can accept them. They do not invent a
C-name-to-machine-register map or force physical registers.

Direct frozen probes were **not successes**: pointer/index order stayed at score 10, two constant
staging variants stayed at 15, and the chain split worsened 15 to 455 (lower is better). Record these
to avoid treating plausible lifetime edits as established compiler rules. Three installed-GCC phase
comparisons preserved each normal object's allocated output with `-da`: pointer/index showed no
comparable phase changes; both constant probes reported scheduling changes. Dump changes can follow
pseudo renumbering and are advisory, not proof of an allocation cause.

An improved, non-exact register-only iterative candidate can earn one phase comparison per frozen
frontier, only with four compiler slots and an 80-second deadline margin available. Reserve that
worst case, charge actual compiles and release unused slots. Unsupported adapters download nothing.
Capture stays separate from the ordinary judge; output-changing diagnostic flags cannot establish
comparable phase evidence or progress. Frontier evidence and a short activity note preserve the lead
without adding full dumps to every agent response.

Controlled comparison against main `944d1d4`: five frozen inputs (four unique routines), same
source/header/target/settings scopes, 96 candidate trials and 120 seconds per input. Both versions
recovered the two historical inputs at trials 35 and 1. The other three stayed at scores 10, 15, 15.
Both spent 324 candidate compiles (329 including baselines); unchanged-output trials across the three
unresolved inputs totaled 193 in each version (81/71/41 on main, 84/67/42 here). No lost recovery,
score regression, new coverage or measured compile-count saving. The earlier 189 figure used an older
main revision. These equal-budget replays exercise repair selection without adopting code; diagnostic
capture was tested separately on copied inputs and with budget/adoption integration tests. Do not
claim that phase capture or these new source families have solved the remaining allocator mismatches.

After integrating main `b3f8aff` (equal-value tracking/allocation follow-ups), the same five-input
comparison again recovered the historical inputs at trials 35 and 1, with unresolved best scores
10/15/15 and 329 compiler invocations on each side. Main recorded 179 unchanged-output trials on
the three unresolved inputs; the fair-exploration/lifetime version recorded 192. Reserving exploration
can spend more trials on source shapes with identical immediate output; this corpus demonstrates no
match or compile-count benefit yet. Restoring the established lifetime-family fallback behind the new
equal-value priorities fixed two integration test failures without dropping either family group.

## Repeated copy helpers and room repair history (November 2026)

Recent examples motivated three bounded shared-pipeline hypotheses:

- `inline_copy_loop`: extract repeated ordinary unsigned-word copy loops. A combined form extracts
  overlap-tested forward/backward copies only when control-flow liveness proves their unescaped
  induction locals dead at every exit. The fallback preserves final pointer/counter values explicitly.
- `typed_command_record`: represent two consecutive ordinary word stores as a candidate-local
  two-word record, retaining pointer-load, pointer-update and payload-store order. Requires an
  explicitly declared word-pointer member; does not infer offsets or modify shared layouts.
- `known_call_excess_arguments`: a matched callee definition establishes arity. Remove only trailing
  integer literals from a single direct call and correct its candidate-owned prototype. Unknown,
  indirect, variadic and side-effecting extra arguments are excluded.

Controlled replay of private example #20529 recovered the historical successful compiler output
from a baseline score of 120. Its extracted function fingerprint and allocated data agree with the
successful control. This is historical recovery, not a new linked-byte match. Graphics examples
#20532–20533 also changed build settings: they motivate a conservative shape family, but do not
establish isolated source-repair success. Generated copy/record shapes also have executable host-C
checks, including overlapping buffers and observable pointer updates. Every proposal still needs
normal compilation and the project's ordinary judge before it can become progress.

Room exact-candidate packets optionally include the worker's captured failed baseline, score,
compiler settings and historical header. After host acceptance, these stay under `worker_baseline`
in the repair example, with a dedicated worker-before/matched diff in the viewer and full exports.
They are explicitly worker-reported, not host-rejudged failures, and never become host attempts,
scores or automatically comparable replay inputs. Host verification of the successful candidate
is unchanged. Source-free exports omit the worker source/header too. Old clients and successes
without a failed baseline continue to work.
