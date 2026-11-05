# Matching KMC GCC 2.7.2 (many third-party N64 games)

Flags are usually `-O2 -mips3 -G0` (some games `-O1` or `-O3`; ask the user to change the project's
compiler settings if nothing matches at all).

When instruction order differs around calls or returns, use `search_flags`: it also checks `-g` and `-g3`
while keeping `-O2`. Debug line labels can prevent the old assembler moving an instruction into a delay slot.
A matching result proves the flags for that function; keep the override scoped to the verified address range.
The automatic compiler survey also checks these debug variants on saved attempts.

## Near-match checklist

Keep the compiler, flags, header and symbol addresses fixed while comparing source repairs.

1. **Too few instructions:** inspect shared tails in repeated branch bodies, then check for missing
   logic. Instruction count alone does not distinguish them or establish equivalent behavior.
2. **Repeated branch bodies:** compare shared, branch-local and mixed pointer lifetimes. Keep a
   result pointer shared while localizing another pointer as a separate hypothesis; localizing
   everything can make allocation much worse.
3. **Right structure, wrong registers:** try plain same-type local declaration order before widening
   the flag search. Avoid adding `register` or changing initializers in the same experiment.
4. **A score plateau:** compare frame size, spills, calls and branch shape. Retain a small alternative
   that fixes one of these even if its total score is worse; keep the saved best candidate intact.
5. **Before repeating trials:** check failed history for the current source, header and compiler
   inputs. A changed context needs rejudging; an unchanged failed trial needs a new hypothesis.

For register-only residuals, compare the linked operand differences and inspect the allocator's
actual pseudo-registers. A source variable and its sign-extension temporary may have different
lifetimes and priorities. One verified 662-instruction case required preserving a raw coordinate
past narrowing and changing the relative allocation order of two saved-register lifetimes; pure
identities disappeared late but affected earlier bookkeeping. This is a case-specific workaround,
not a default recipe. Broad register qualifiers and retyping did not help; some attempts added an
eight-byte stack-frame difference. Keep the target and flags fixed, retain failed input keys, and
require exact linked bytes. Details: `docs/COMPILER_MATCHING_LEARNINGS.md`, “register allocation
after late cancellation”.

These lifetime/declaration observations come from KMC GCC experiments. They do not establish an
IDO rule. IDO's separate observed source-layout sensitivity is described in `guide("ido")`.
Every proposal still needs the normal linked-byte and data checks.

Traits
- Unconditional jumps inside a function are `j label`; IDO would use `b`. (That's how the app guessed.)
- `move` is written as `addu rd, rs, $zero`.
- Branch-likely (`beql`, `bnel`) is common in loops and simple ifs.
- The function's local variable declaration order affects register allocation and stack slots.
  Reorder declarations before rewriting logic.
- Expressions are evaluated mostly left to right; swapping operands of `+`, `==`, `&&` can change
  register use or instruction order.

Things to try when registers differ (`r`)
- Introduce or remove a temporary variable; reuse one variable for two values.
- Change `int` to `s16`/`u8` (or back) for locals and parameters; signedness changes comparisons and
  extension instructions.
- `for` vs `while` vs `do { } while` loops produce different block layouts. `do-while` has no
  initial check.
- Pre/post increment, `x += 1` vs `x++`, pointer walking (`*p++`) vs indexing (`p[i]`).
- Early `return` vs `if/else` with one return at the end.
- `if (a) { if (b) ... }` vs `if (a && b)`.

Calls and prototypes
- Without a prototype, GCC assumes `int f()` and promotes arguments: floats become doubles,
  `s16`/`u8` arguments are not truncated. Missing extensions or `cvt.d.s` before calls point at
  prototype problems: add the prototype (edit_context).

switch
- Dense cases become a jump table (`jtbl_` in .rodata, `jr $reg`); sparse ones become compare chains.
  Your jump table must equal the ROM's, so case values and order matter.

Measured large-function findings (`-O2 -g3`, 32-bit MIPS)
- Try a bounded small `__builtin_memcpy` when explicit loads/stores copy a short string. Preserve the
  source array's actual size/alignment and use a real destination buffer; never infer contiguous locals.
- Recover real records and packet/command fields before broad declaration shuffling. Typed memory
  accesses can change alias analysis, scheduling and registers.
- Try removing synthetic one-shot wrappers, splitting a command pointer into a small block, and sharing
  an array field address with a negative offset back to its base. Lifetime changes can need paired edits.
- Keep a bounded alternative even when total score gets worse: it may fix one region and allow the next
  edit to finish the match. Never replace saved best C with an unverified regression.
- These were observed on one 883-instruction function, not established for every GCC version. The
  repository's `docs/COMPILER_MATCHING_LEARNINGS.md` records evidence, failed ideas and update rules.

Register-only finishes
- A masked or narrowed value may need an existing local, rather than a new temporary or an in-place
  assignment to its input. Try `scratch = (u16)width; centre = (limit - scratch) / 2;` only when the old
  `scratch` value is dead on every path. Preserve resets before later uses, including loop back edges.
- `run_local_repairs` offers `masked_value_reuse` and `cfg_integer_lifetimes`; autopilot's repair phase
  and deep search use the same rules. Draft lifetime hints keep m2c's original names and suggest possible
  groupings. They are advice, not proof of the original declarations or a match.
- Check actual register/opcode differences before searching constants. A numeric load offset and
  `%lo(symbol)` can render differently while encoding the same operand. Search guidance normalizes
  resolvable operands; the displayed diff, score and compiler/link judge stay unchanged.
- Keep an instruction-order alternative alongside a register-only alternative. Numeric similarity
  alone can discard a source shape which a later small edit can finish.

Expression and frame shape
- Address association can change allocation/order: compare `(base + displacement) + index` with
  `base + (displacement + index)` while preserving pointer bounds, types and evaluation side effects.
- An optimized-away conditional or comma-expression placement can still change this old compiler's
  intermediate lifetimes and final scheduling. Treat this as a measured source-shape experiment,
  not a reason to invent arbitrary predicates or read uninitialized values.
- An inline helper's aggregate/bitfield local affected stack-frame reservation in one verified case.
  This is an observation, not a general padding rule. Inspect all stack offsets and verify the bytes.
- Keep the successful source, header and compiler profile together. Do not switch compiler hosts or
  repeat wide flag sweeps once controlled comparisons show identical output for the relevant input.

See `docs/COMPILER_MATCHING_LEARNINGS.md` for controlled evidence and failed experiments.

### Address association and call initialization

Equivalent byte-pointer expressions can schedule differently in KMC GCC. The bounded
`address_association` family tries constant displacement grouping, including candidate-owned
inline helpers. Calls and incrementing expressions are excluded from these transformations.
The `call_initialization` family can move a constant local reset into a call's first argument
when scalar CFG analysis proves every relevant path reaches that call before a read or
redefinition. Unknown syntax, escapes and unresolved loops are rejected.

Stack diagnostics distinguish frame size, register-save offsets and other stack operands.
Do not infer an object's size or add padding from the frame delta alone. The
`experimental_bitfield_frame` local family is available only by explicit selection; it is
excluded from automatic repair defaults. Its compiler-specific signed-halfword helper storage
is an experiment, and every candidate still requires the ordinary exact judge.

#### Inline boundaries and repeated bounds tests

With KMC GCC 2.7.2 `-O2 -g3`, an inline scalar helper can change front-end expression
shape, register allocation and scheduling even when it emits no call. Repeated axis tests may
need the same helper form together: one controlled six-comparison regression recovered an exact
linked match with the grouped form, while single-site changes did not. Use the local
`inline_scalar_shape` family before rewriting each site manually. `combined_scheduling` offers
bounded bridges with temporary, stack-address or loop shapes; the normal judge must verify them.
This is compiler-specific evidence, not a promise that helpers improve every function.

### Near matches with calls and pointer fields

Use `target_call_argument` when a candidate-owned no-argument prototype conflicts with an
explicit target delay-slot argument load. `known_address_alias` tries auto-address aliases
through one byte-pointer base. Single-field records, their raw-access alternatives and direct
boolean masks can change scheduling. Try both splitting a call-result temporary and reusing
a compatible dead result slot; neither is consistently best. The bounded local families also
combine these changes and can stage field loads in target-observed offset order. Every trial
uses the ordinary judge; do not change shared prototypes or accept a lower score as a match.

### A few remaining register operands

With `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, a verified large routine needed separate
lexical blocks for two draw-pair paths, block-local coordinates, and staged second-call arguments.
The compiler merged their common tail and recovered the last eight register operands. An inline
helper instead enlarged the frame and reordered instructions. Try the combined local-block shape
when a shared tail keeps a converted coordinate in the wrong saved register; preserve every call,
argument value and side effect. Braces alone are not a general fix. See the matching learning log
for the measured case and failed alternatives; every candidate still needs the ordinary exact judge.

The local families `branch_tail_lifetimes`, `staged_call_arguments` and
`combined_call_lifetimes` automate a bounded subset of this experiment. Use them when offered
for register-only misses; unsupported shapes still need a manual edit. Compare frame size and
instruction count before pursuing helper-based detours, and consult current-input local search
evidence before repeating failed variants. A linked exact verdict overrides symbolic annotations.

### Normalization across calls and partial frame gaps

When a saved normalized integer is used after a setter call, try `call_return_normalization`:
an inline helper narrows the argument, calls the unchanged setter and returns the narrowed value.
This recovered a measured two-register near miss by keeping the value live across the call.
`split_narrow_lifetime` can separate an earlier full-width stride from a later halfword-only saved
value. Its mirrored earlier writes preserve the fallback when the later phase is skipped.

If earlier stack slots already agree but later spill slots and the frame differ, whole-frame padding
is the wrong hypothesis. `spill_normalization` tries bounded bundles of late signed-halfword helpers
before low-bit masks. KMC can retain unused spill reservations for eliminated pseudos; six such slots
recovered one measured 48-byte gap. The helper must preserve all observed mask bits. Treat this as a
compiler-specific search hypothesis and verify every offset and the linked bytes. Equal numeric
scores do not rule out progress toward the right offsets; never accept the tie-breaker as a match.

For constant packet stores, `constant_index_temporary` can expose a different allocation by assigning
zero and actually using it as the array index. An unused zero assignment is a different experiment.
`balanced_call_references` tries small identical-arm combinations around related calls; matching one
call site's source shape independently can change allocation elsewhere. Both families are bounded.
For persistent cases, repair replay's optional GCC phase summaries compare normal and diagnostic output;
use its edit ablation to reduce accumulated changes under frozen settings before attributing a cause.

For register-only misses, prefer `pointer_call_staging` and `pointer_load_inline` when offered;
then try the bounded `paired_pointer_declarations` fallback. Replays showed declaration order
can be incidental to a successful pointer rewrite. `word_zero_test_reuse` can preserve unused
local declarations and reuse one same-width signed/unsigned local for a zero-only definition
range, keeping later loop phases intact. Field-store swaps require alias/volatility evidence;
an indirect RHS load is not automatically safe to move. Historical judge revisions need a
fresh fixed-context replay, rather than comparing old scores directly.

For a register-only near miss, `record_value_forwarding` can compute a scalar directly
into an existing, same-type unescaped record member and forward its value to later uses.
One fixed-profile replay recovered a final score 15 → exact; no padding or layout was invented.
`structured_backedge` now supports nested switches and internal/outgoing gotos while rejecting
outside entries and changed break/continue destinations. A replay improved 5950 → 4770.
Natural loops can change allocation and caller-save choices even when their CFG matches goto C.

`narrow_definition_lifetime` keeps an explicit halfword cast in a separate short-lived local,
without narrowing a later wide index that reused the old variable. `sink_zero_initialization`
tries the zero in an else arm only when the other arm definitely assigns first. Both are bounded
hypotheses, not advice to change every declaration or initialization. Random candidates remain
exploratory; quality evidence retains pre-search checkpoints separately from the lowest score.


### Allocation before final instruction order

A 737-instruction private function matched with KMC GCC 2.7.2 and unchanged
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The long search took about 4 h 50 min. Useful
steps included a true scalar spill instead of an explicit stack field, pointer declaration order,
phase-specific counter/table split and reuse, a narrow constant for a byte-fill loop, and a live
vector pointer used by calls while retaining a direct aggregate destination.
The last adjacent initialization swap was replayed through the new automatic family: score 60
became exact linked bytes. This is recovery of an existing match, not a new function.

Do not tune setup order before allocation is plausible: 896 legal setup orderings failed at an
earlier checkpoint. A lower score also hid an enlarged frame. Compare register substitutions,
frame size and spill offsets alongside score, and preserve structurally different alternatives.
Use `allocation_initialization_order`, `pointer_argument_alias`, `spill_declaration_order`,
`byte_fill_constant` and the bounded `allocation_schedule_bridge` when offered. They automate
subsets of these observations, not the complete historical search. Exact results do not prove
original variable names, types or source spelling. See `docs/COMPILER_MATCHING_LEARNINGS.md`.

For stubborn misses, Repair examples' GCC diagnostics now compare frozen before/after allocator and
scheduler phases. Compare `lreg`/`greg` assignments and lifetimes with `sched`/`sched2`/`dbr`, only when
both diagnostic builds preserve ordinary output. Pseudo numbering changes are not causal proof. New
captures can run bounded ablation automatically; use the retained edit set as a hypothesis for a local
family, keeping the original exact source until a separate ordinary judge accepts any replacement.

When repeated branch bodies compile too short, inspect unwanted tail merging or missing logic. Try
`branch_pointer_lifetimes`: keeping a result pointer shared while splitting name pointers can outperform
both all-shared and all-local forms. `branch_pointer_declarations` follows these bounded combinations with
plain declaration swaps; one private replay recovered the existing exact output on proposal 17. The shared
frontier and failed-trial cache retain alternate shapes and avoid repeating unchanged proposals. Candidates
still require the ordinary judge; matching one compiler output does not prove original source spelling.

For a register-only miss with already-correct instruction structure, inspect allocation
priority as well as interference. In one verified case, small nested single-execution
`do { ... } while (0)` scopes changed loop-depth-weighted reference counts and recovered
register choices. Placement also changed scheduling and introduced a copy, so scopes
alone were insufficient. Restoring a coordinate from an existing equal saved value kept
that value live after a conditional call and removed the final copy. Prove the equality
on every path; preserve evaluation/call count, wrapping and object lifetimes. Never add
such scopes around control transfers whose targets would change. This is a bounded
KMC-specific hypothesis, not evidence that the original game used those wrappers.
See the 2 November one-shot-scope case in `docs/COMPILER_MATCHING_LEARNINGS.md`.

For allocator-only near misses, `balanced_pointer_calls` tries initialized pointer guards with
identical call leaves, including bounded nesting. `paired_integer_lifetimes` combines a CFG-proven
value-phase split with disjoint reuse. `initialization_cluster` groups independent pointer/scalar
resets and safely extracts comma initializers. Try these local families with the recorded profile
before spending more model turns. A frozen initializer case recovered exact linked output in 17
compiles; a preceding pointer shape needed 108 compiles and three families. The broader earlier
checkpoint was not automatically recovered in the measured budget. Regional frontiers can retain
useful intermediate shapes with a temporarily worse total score; the ordinary exact judge still
decides acceptance. Use the read-only linked-ROM benchmark to measure a private checkpoint.

For register-only near misses, request `equal_value_increment` and `loop_weight_scope`
through local repairs before broad random trials. Both use the existing budget and judge.
Scope proposals change only plain scalar updates; equal-value proposals currently require
an adjacent copy and increment of identically typed locals. Cross-branch lifetime changes
still need investigation. Preserve a distinct allocation candidate even if its numeric
score is worse. Optional GCC diagnostics expose loop notes and legacy reference weights;
use them as leads only when diagnostic flags leave output unchanged.
Compare canonical linked function bytes, not whole ELF hashes: diagnostic options
can change ELF metadata without changing generated code. In a controlled
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3` decoder, `-da` exposed a structured-loop
hoist of a channel shift. A real goto backedge prevented it and restored the
target frame (0x58 → 0x50), but worsened the score (2454 → 2650). A correct frame
is a localization clue, not an acceptance criterion. Preserve the actual
iteration, memory effects and input contracts when testing loop shapes.

For missing halfword masks, check early RTL before searching register allocation.
A controlled 305-score near miss contains both zero extensions initially; first
CSE removes them by reusing already narrowed inline-getter values. Real
switch/fallback forms and value transports did not recover them. This is a
localized negative result, not a successful repair family. Do not preserve an
incorrect predicate merely because it scores lower.

For mixed allocator/scheduling misses, inspect the compact first-differing-block
report before requesting another full diff. Local search now interleaves block and
whole-function repair priorities and bounds combinations of regional improvements.
This does not prove a lifetime conflict: the frozen score-520 regression remained
unresolved after 128 candidate compiles. Captured single/pair edit subsets can help
separate sufficient changes from accumulated experiments under the same compiler.

For repeated halfword subtractions with correct instruction structure but swapped operand registers,
try `paired_operand_reuse`: stage both operands through the same pair of existing dead word locals.
Use CFG liveness across branches/backedges, not textual last-use guesses. Unsigned halfword staging
is only a modular-store hypothesis; keep signed operands for a full-width difference.

A remaining initialization-order miss can originate in induction-variable creation. Try deriving an
independent positive-stride counter from a zero-based unit-step index before its reads. The bounded
`affine_counter_guard` family also removes one identical-arm scalar guard at a time; another guard
may still be necessary for allocation. It rejects incoming jumps, escaped/mutated/live-out counters,
and unsupported loop shapes. Pure scheduling and mixed register/order clues prioritize these families.
A private 48-trial replay recovered the historical exact result from score 160 in 43 trials (~33 s),
and from score 60 in one trial (~3.2 s), while main recovered neither. These are rediscoveries;
ordinary linked-byte/data verification is still required. See the 2 November paired-operand case
in `docs/COMPILER_MATCHING_LEARNINGS.md` for the compiler profile, failed alternatives and limits.

When a paired-operand or affine-counter edit helps, iterative search now gives its complementary
family a bounded early window while keeping other families and ordinary queued work. Explicit changed
memory offsets can rank operand sites; C names do not establish register identities. Parent/pool cursors
retain generated candidates beyond the old shortlist across runs, under the existing separate budget.
In a five-checkpoint private comparison against main `f399309`, score 160 recovered exact on trial 35
(~33 s), while main remained at 100 after 96 trials; three fresh near misses were unchanged. This is
historical rediscovery, not new game coverage. See the matching learnings and repair-replay docs for
bounds, cache scope, unsuccessful cases and the unchanged exact-code/data requirement.

For tiny register-only misses, try a small lifetime/evaluation change before another whole-function
shuffle: stage a positive store constant in a local, separate same-type local assignment chains,
or reverse a pure pointer/index addition. These are hypotheses, not known fixes: three frozen misses
remained unresolved, and one chain split made allocation much worse. Keep the ordinary linked-byte
judge. After two unchanged code/relocation outputs, iterative search now defers that family's siblings
on the same parent while retaining exploration; changed output or a different parent gets fresh evidence.
A promising improved candidate may receive one budgeted installed-GCC phase comparison. Phase dumps
must preserve normal allocated output with `-da`; scheduling/lifetime leads do not prove register identity.


`cfg_equal_value_increment` now tries earlier equal locals across proven CFG joins and
calls when neither address escapes. It handles modular unsigned increment/decrement
restoration and literal-zero do latches. Unknown writes and uncertain paths discard
facts. Try it with `loop_weight_scope`: the bounded iterative search reserves complementary
follow-ups without increasing the phase budget. Saved-case output replays recovered a
previous long-running near miss in 17 or 29 candidate compiles; this is regression
evidence, not a guarantee for other functions.

Use Finish near matches for a budgeted GCC value trace when a register-only plateau has no promising
first edit. GCC's instruction IDs, line notes, def/use/death records and explicit register dispositions
can narrow candidate lifetimes; a reused register can still correspond to several values. The trace
must preserve normal allocated output. It costs up to two compiler runs from the same CPU budget,
not an AI request, and cached traces reject changed source/header/settings. Small traced two-edit
bundles and actual verified same-unit private helper/static-constant definitions are ordinary judged
hypotheses. The latter recovered a homemade inline-helper test, not an additional game function.
Do not infer private calling conventions or object definitions from an extern. Experimental priority
boosts need exact-throughput wins on disjoint-target held-out replay; plausible source shapes alone
are insufficient. See `docs/repair-replay.md` for limits and the reviewed promotion workflow.

If the original materializes the same address twice while the candidate derives it from
a nearby pointer, try `independent_address_materialization`. It uses a fresh automatic
symbol resolving to the same address to keep the compiler expressions independent.
The target must support the repeated address pairs, and the normal linked judge still
settles the result. A frozen case improved from 155 to 25; its remaining register miss
was not solved by the tested local variants or scheduler toggles.


When comparing failed/successful GCC dumps, never join pseudo numbers across builds. Use unique
unchanged C statement anchors under the same header/profile and omit multi-value ambiguities.
A bounded minimized repair is only sufficient under its frozen control; require disjoint-target
exact validation before importing an expression recipe. If an old exact source survives a rename,
use Recover saved matches to rejudge it with recorded/current headers instead of trusting its old score.

For a register-only table-index store, try `pointer_index_scope` when the source already
has a block-local slot pointer. Splitting its pointer and index into typed locals inside
a one-shot scope recovered one 52-instruction KMC `-O2 -g3` case exactly. The split alone
made that case worse: test the combined shape before discarding this lifetime hypothesis.
The rule is bounded and retains the normal exact judge; it is not a general register fix.


For a tiny register-only residual with identical instruction forms and frame, try existing
`allocation_schedule_bridge` then `pointer_index_scope` before broad type/flag sweeps. This sequence
recovers one frozen control in four trials on current main; it is not a universal recipe. Retain
measured equal-score outputs, since different allocation shapes may enable the next repair. Small
independent lifetime edits can also be paired within the iterative budget; the linked judge remains
mandatory. If a register is reused across the function, a global rename map cannot identify a C value.
Do not generalize the successful pointer/index scope to every register-only miss. Four
other frozen KMC cases stayed unchanged after pointer/constant staging, dead-local reuse,
shift-chain rewrites, command-accumulator/tail combinations and one-shot scope alternatives.
Consult the negative-results table in `docs/COMPILER_MATCHING_LEARNINGS.md` before repeating
these families. Multiple pseudos can share `v0`; use GCC instruction IDs/source notes,
not a physical register name alone, to select a lifetime hypothesis.


Macro-safe pairing can now work outside unchanged simple defines, with source and header macro
dependencies protected. It will not move edits across opaque macro effects. In a tiny register-only
constant-store residual, a small local expression is not always enough: the remaining CFG and value
lifetimes can determine allocation. A reduced compiler reproducer is diagnostic evidence, not a
semantics-preserving game repair. Neither this investigation nor the broader five-profile replay
established another automatic match, so no new priority recipe was learned.

### Check global identity before chasing the last scheduling NOP

For a near-exact KMC function, inspect whether repeated reads of the same proven
global cell mix a byte-array-base-plus-offset expression with a bare integer
pointer. A consistent typed external declaration can change CSE, lifetimes and
scheduling even when the old expressions resolve to the right address. Keep
other globals distinct and preserve volatility; verify any candidate-local change
with the normal byte judge rather than editing shared types on resemblance alone.

In one measured 1,023-instruction renderer at GCC 2.7.2 / GNU as 2.6,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, scoped integer accumulators reduced 550 to 440,
and consistent typed global access then matched exactly. Moving a colour load
manually only reached 80; packing rewrites and a ten-minute deep pass did not solve
it. An inserted NOP had shifted many later branch offsets. Diagnose the first
actual byte mismatch before interpreting those downstream rows as separate faults.

The `typed_global_identity` local family now proposes this specific case: repeated
pointer-cell reads through an absolute cast and/or a simple byte-array-offset macro,
with the cell address corroborated by raw target instructions. It introduces a
candidate-local typed extern and unifies those reads. Volatile/qualified cells,
non-byte bases and conflicting declarations are excluded. It does not infer a
pointer cell from a similar-looking pointee address or change shared headers.
A private replay generated the known 440-to-0 exact result automatically.

`block_accumulator_lifetime` tries independent integer accumulators in existing
blocks when the outgoing value is dead. A private replay reproduced 480-to-440;
it is a bounded hypothesis, not a guarantee that every local should move inward.
Keep structurally different near misses: the lowest numeric score can be a poor
starting point for a declaration/context repair. Deep search now reserves half
its remaining time and compile allowance for random rewriting after the initial
local phase (one in-flight compile can cross the time boundary).

### Call scopes, phase storage and grouped initializers

For a moved argument instruction near a call, try `call_one_shot_scope`: a single
`do { call(...); } while (0)` can constrain KMC scheduling without duplicating the
call or changing its arguments. The generator excludes control-bearing macros
and already wrapped calls. This is a compiler-shape experiment, not a loop the
original game must have contained.

`pointer_integer_phase_union` proposes separate union members for a pointer phase
and a later integer phase only when CFG liveness proves non-overlap and every read
has a dominating initialization. It never reads a pointer's representation as an
integer. Narrow integer values keep their original casts at reads and writes.
Escaped storage, captured macro locals, overlapping lifetimes and unknown ABI
widths are excluded. Do not hand-edit assembly registers to resolve a closed swap.

With known debug flags, `debug_initialization_group` joins existing initializer
statements; `debug_constant_initialization` can stage an already repeated float
literal and group the run. A leading initializer may move after independent
private address calculations, preserving their dependencies. Physical line groups
can change scheduling with `-g3`; avoid reformatting a verified source wholesale.
`__LINE__`, line directives and opaque macro effects are not safe grouping targets.

Controlled replay: a previously matched 519-instruction sound update, KMC GCC 2.7.2 /
GNU as 2.6, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. From a saved score-495 alternate,
the generated union, constant-initialization and call-scope steps yielded
495 → 200 → 60 → 0, with exact linked bytes at the end. Separately, initialization
staging reduced a score-180 seed to 40; its remaining eight rows formed a closed
`s0`/`s1` swap. Numeric proximity alone did not select the successful path. These
replay results establish coverage of this known match, not recovery rates for
other functions or evidence of the original source spelling.

## An exact operation can still need a different temporary lifetime

A measured 567-instruction function under KMC GCC 2.7.2,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, had just one remaining addition with reversed
source operands. Commutativity did not make its bytes an exact match. A fresh
subtotal preserved operand order, but the local allocator merged the dying
subtotal with the result. Reusing an earlier handle prevented that merge but
changed the early instruction schedule. Reusing a compatible scalar with a
separate **later** lifetime reproduced the complete function exactly.

The `later_local_reuse` family now tries this bounded repair before broad mutation
for register-only cycle misses. It checks CFG liveness: the donor's old value must
be dead, its declaration must be in scope, and its later value must be assigned
before use on every path. It rejects escaped storage, incompatible/unknown widths,
qualified locals and unknown macro effects. It can pair a compound sum's separate
subtotal with the reused destination. No padding operations or fixed registers
are introduced. All candidates still require the normal exact judge.

Retain alternatives that repair the original target locations even if a new
scheduling difference raises their score; neither score nor source equivalence is
an acceptance verdict. The search reserves one such alternative within its
existing beam and budget. Recognize integer typedefs through the target ABI, not
the host: a verified 32-bit `long` alias can be compatible with `int`, while a
64-bit/qualified/ambiguous alias is not.

A private frozen pre-match replay recovered this case in three candidate compiles
plus one baseline compile (about 1.7 seconds locally). This is a regression result
for one saved source/context/profile, not a general runtime guarantee or a new
match. The real accepted source also passed the complete cartridge rebuild.

### Follow the new residual, and keep reuse type-safe

When a measured alternative fixes the original operation but exposes a new schedule or
allocation miss, try a small follow-up on that alternative before broad mutation. The
search reserves up to two probes in each of three bursts within its existing budget;
a worse intermediate is retained only as a hypothesis, never adopted as a match.

Later-local reuse also supports ordered subtraction, bitwise operations, unsigned
32-bit literal shifts and explicit same-width call results. A dominated acyclic branch
can be safe; a live loop or bypass join is not. Keep operand order, conversions and
single call evaluations. Unknown macros, escaped locals, uncertain widths and ambiguous
prototypes disable these hypotheses. Same-input, uniquely mapped GCC allocation lines
can rank donor lifetimes by call crossings; stale or ambiguous mappings give no advice.
See the matching-learnings ledger for frozen multi-case replay commands and limitations.

A four-case equal-budget replay retained the existing candidate-3 exact recovery, with
no additional recoveries or score gains on the other three. Keep the short direct donor
lane ahead of residual detours. A remaining acyclic register chain needs different
allocation targeting; a jump/delay-slot residual should not be treated as a literal
constant change. The wider hypotheses are supported/tested, not evidence of a general
speedup or extra matches.

### Register-only does not require a closed global cycle

A pointer-load chain can use different registers locally even when reused physical registers
prevent a single global substitution map. Search now gives proven local acyclic chains up
to eight existing source-lifetime/declaration proposals, within the normal budget and
without extra diagnostic captures. This is allocation advice, not permission to substitute
register names or infer original variable names. Ambiguous runs and changed frames/counts
do not get this lane.

A differing jump destination or delay-slot instruction is a control-flow residual, even
when the displayed marker is `i`. Try structured backedges, guard/goto/branch orientation or
loop-update placement before treating a label as a literal constant. Calls and true literal
changes keep their own priorities; all acceptance still requires exact normal judging.

Four frozen cases retained the same exact recovery and best scores under equal budgets;
these additions have not yet demonstrated an extra match. A moved instruction multiset
keeps scheduling priority, and a literal-only edit inside an existing delay slot remains a
constant residual. The compiler-matching ledger records failed trials as well as recoveries.

### Separate branch-local coordinates before forcing a cast register

For a wide coordinate used by one call and then narrowed for another, separate
branch-specific locals can matter even when a single function-wide scalar has the
same C value. In a newly matched 253-instruction case (`-G0 -mips3 -mgp32 -mfp32
-O2 -g3`), the separate lifetime made the cast's intermediate shift reuse the
dying coordinate register. An explicit in-place shift alone was optimized back
into a differently allocated temporary.

An actually inlined ordinary-C helper for the real decrement/call/returned-count
sequence fixed a different argument-setup ordering issue. Finally assigning the
coordinate inside its call argument, and retaining it for the next call, recovered
the remaining schedule. Keep alternatives that fix a particular instruction even
when another allocation changes; their useful transformations may combine.

Check object ownership and aliases first: moving a store onto the wrong constructed
object is not a valid repair. A fresh snapshot of a global pointer before byte
stores may expose the target load order, but only where those accesses and aliases
permit it. Preserve real widths, call effects and nonoverlapping lifetimes. These
hypotheses still require the ordinary exact linked-byte/data judge. This case
passed that judge and standalone layout checks, not a complete ROM rebuild; see
`docs/COMPILER_MATCHING_LEARNINGS.md` for the measured steps and failed trials.

The automatic families for these shapes are `branch_scalar_lifetime`,
`inline_call_count`, `embedded_call_assignment`, `extracted_call_assignment` and
`typed_pointer_snapshot`. Autopilot/deep local search share them. Complementary
repairs use the existing budget and measured frontier; they do not spend AI tokens.
The helpers require an explicit compatible prototype and must actually inline.
Snapshot ordering currently needs MIPS target evidence and known byte fields.
Unsafe aliases, cross-argument dependencies, escaped locals and opaque macros are
left for manual investigation. An available family is not a guarantee of recovery.

Controlled private replay recovered three saved precursors of this **same known
253-instruction function** at trials 14, 17 and 1, versus none on main under an
equal 64-candidate allowance. The earliest precursor improved but stayed unresolved.
This demonstrates coverage of these source shapes, not three new matches or a
recovery rate for other games. Keep the failed precursor in future comparisons.

### Stage a narrow call argument through a dead word local

When the target already has the correct signed/unsigned short conversion but its
intermediate register differs, try `narrow_call_destination`. It uses the existing
callee prototype and a CFG-proven dead scalar; it can stage the same phase in
separate branches. Even a numerically redundant conversion can expose a useful
new lifetime. Do not discard it solely because its C value is unchanged.

Autopilot and deep search can earn a small continuation beyond the normal four
steps after repeated, measured complementary progress. The allowance is at most
two extra levels and four proposals, inside the existing budget. Unchanged output
families are deferred across equivalent source parents, retaining a third direct
probe and occasional exploration. None of this supplies an exact verdict; the
ordinary compiler/link/data checks remain mandatory.

### Local repairs and measured sequence history

Use line associations from the current compiled object to narrow a standalone
call/operand repair. Do not treat an assembly window as a C-line map, or split
connected declaration/lifetime/control-flow edits. The app interleaves bounded local
hypotheses with the original whole-function candidates; missing debug information
keeps ordinary search. Changed-output and score-improvement history can suggest
continuations under the same compiler/flags, but only the exact judge proves a match.
Imported ELF trials now retain their function-output fingerprint before restoration;
COFF section-level evidence is useful for rotation, not an earned deeper function path.

Before permuting a register-only tail, check the function's return contract against
its callers. A caller consuming `$v0` can expose a draft incorrectly declared `void`;
returning the already computed index may restore the missing copy and reserve the
right registers. Once the ABI agrees, compare a byte-narrowed packing temporary with
its equivalent repeated inline expression, keeping the conversion at each use. A
173-instruction live case recovered exactly through that sequence. Plain float-array
storage also restored its call-delay-slot store; adding `volatile` had blocked it.


For the KMC-compatible GCC 2.7.2 pack, `__builtin_mips_debug_break()` is an app-provided
fixed BREAK 9 compatibility intrinsic for debugger/assertion paths. It is not native
ISO C/GCC 2.7 syntax, takes no arguments, and can resume afterward. A noreturn annotation
or an unresolved `__builtin_trap` call does not substitute for that hardware operation.
Keep submitted assembly forbidden and verify the complete linked bytes as usual.

With `-O2 -g3` (DWARF), a branch-tail float-to-int conversion can need a block-local
integer temporary on separate declaration, conversion and copy lines. In one verified
reference, this placed the assembler's hazard NOP before the common branch label;
the one-line equivalent branched to the NOP instead. Both execute equivalently, but
two branch encodings differed (score 20 versus linked score 0). Preserve tested line
breaks and verify raw bytes; do not relax the judge to accept equivalent NOP targets.

### Return contracts and narrow expression lifetimes

Before register permutations, inspect an explicit target return-register copy and
actual callers consuming it. A void draft can hide the intended returned scalar.
`return_contract` tests this without changing shared prototypes.
`narrow_expression_inline` repeats a pure expression with its byte/short conversion
preserved at every use. `plain_float_slots` tests ordinary local array storage only
with target call-delay-slot stack-store evidence; never strip MMIO or pointer volatility.
These families compose under ordinary budgets. Every hypothesis still needs the normal
exact judge; missing target evidence is no reason to guess a contract.

### Ordered instructions agree but copied registers differ

With GCC 2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, a copied-from scalar can
remain the allocator's preferred operand even after an explicit copy. Use the
`dead_copy_source` local family when the original local is dead on every outgoing
control-flow edge, unescaped and nonvolatile. A dead reset can disappear while
changing copy propagation. Never clear a value still read on another branch or
loop iteration. `divisor_normalization_copy`, `divisor_value_scope` and
`divisor_flag_split` separate saved arithmetic values from scratch flags without
inventing a different division or rounding rule.

For hazard NOP/scheduling differences with debug flags, try
`debug_statement_boundary`: physical line boundaries can change this legacy
compiler/assembler output even when C tokens are unchanged. Keep successful line
layout. This is measured behavior of a specific toolchain, not a rule for all GCC.
Prefer these bounded local variants and source-mapped allocation diagnostics to
repeated full assembly/context requests or random declaration shuffles. The normal
judge remains the only match verdict.

### Conversion joins and shared shift intermediates

With the original `-g3` profile, try `debug_conversion_tail` for branch-target
near misses around `mfc1`: a same-type conversion temporary on distinct physical
lines can place the shared label after the assembler's hazard NOP. Keep successful
line layout intact in saved C, exports and polishing; rejudge any formatting edit.
`delayed_narrow_capture` can let a known narrow call parameter perform the ABI
conversion before capturing that value after the call. `shared_shift_mask` tests
redundant masks justified by an intermediate's known-zero low bits to preserve CSE.
These bounded local families are hypotheses and still require exact linked bytes.

If m2c emits `M2C_BREAK(9)` and the target has BREAK 9, use the fixed
`__builtin_mips_debug_break()` supported by this compiler pack. It may resume;
`noreturn` and an unknown trap builtin are not substitutes. Find missing non-NOP
operations before interpreting downstream branch displacements or extra padding
as progress. Other compiler packs need their own tested intrinsic registration.

### Branch destinations around a hazard NOP

A two-branch miss can come from a physical source boundary: the NOP may land before
or after a shared label. Check identical branch tests and one-word destination shifts
before more register permutations. `hazard_statement_lines` tests bounded boundaries
and same-type assignment-conversion temporaries. Preserve the exact working lines.
For a shared doubled value, `known_zero_mask` can preserve a useful intermediate through
a range-proven redundant mask. `post_call_narrow_capture` retains the explicit argument
conversion and captures the local afterwards, only with no intervening mutation/escape.
These supplement existing conversion families; every candidate still needs exact checking.

### Current live return address versus the entry caller

When target code stores `$ra` to a global after an optional callback, distinguish
that hardware snapshot from `__builtin_return_address(0)`: the latter returns the
function-entry caller even after another call changes the live register. The fixed
no-argument `__builtin_mips_live_return_address()` in this compiler pack captures
the current register with an ordered compiler-visible read. It is not portable C
or a request to synthesize instructions; submitted assembly remains prohibited.

Try `live_return_address` when a draft's uninitialized placeholder corresponds to
a raw target-proven absolute `$ra` store. Do not run broad flag sweeps for a missing
hardware operation, guess a caller constant, or reinterpret stack frame saves as
this pattern. All resulting candidates still require ordinary exact verification.

For a live-register store followed by a persistent register-allocation mismatch,
try `live_return_address_initializers`: preserve one-shot scopes, inline an
unchanged typed integer literal, or stage adjacent literal declarations while
keeping initializer evaluation order. A hardware-only intermediate can score
worse, so test the bounded combined proposal directly. Unrelated volatile locals
are retained; volatile placeholders are not inferred. Verify the function's own
compiler profile rather than copying a neighbour's flags.

A source repair and compiler profile can both need changing at once. One saved
`-O2 -g3` draft scored 5; the live-register repair scored 65 there but exact with
an independently verified neighbouring `-O2 -ffast-math -fno-schedule-insns`
profile. Neither source-only nor profile-only improvement would find this pair.
Check that specific pair before a broad sweep. Match popularity can rank the
correct local profile well below the default; multi-profile deep search visits
only the explicitly selected number. Keep single-profile searches default-only.

When the remaining miss is the register holding an early pointer, try sharing its
storage with a later word scalar through `pointer_integer_phase_union`. A final
call such as `code = inspect(pointer, pointer->kind)` reads the pointer before
writing the scalar. An initialized union can preserve this allocation identity
without reading a member as another type. Keep the initializer in its original
position. Require a defined scalar phase, no subsequent pointer read on any CFG
path or loop back edge, unescaped storage and explicit 32-bit pointer metadata.
The normal linked byte/data judge must verify every proposal; later word compound
updates keep their original type, while narrow compound updates stay excluded.


A final `addu` operand-order miss can be hidden inside a byte-load macro.
For a proven `LOAD(p,o) (*(char**)((char*)(p)+(o)))` shape,
`LOAD(LOAD(base,offset),index)` may need the outer address written as
`*(char**)(index + (int)(LOAD(base,offset)))`. The bounded
`macro_byte_address_order` repair preserves both reads and leaves directives
unchanged, requiring a literal field offset, word index and explicit 32-bit ABI.
Isolate each edit: an accompanying signedness change need not contribute.
Do not expand unknown macros or reorder side effects; verify linked bytes/data.

If a draft already uses `__builtin_return_address(0)` for a raw global `$ra` store
following a callback, the same family tests the fixed live intrinsic and an atomic
`do { global = (s32)__builtin_mips_live_return_address(); } while (0);` boundary.
The direct swap can worsen the score by allowing mask arithmetic to move before
the store; judge the bounded companion even then. This requires encoded target
call/store evidence and a declared 32-bit scalar or pointer destination. Genuine
entry snapshots without that evidence stay unchanged. Keep physical source lines
when testing with `-g3`.

Check again can retire legacy agent-only tooling verdicts about this hardware read
when the active compiler supports the fixed intrinsic and the existing source has
a target-backed shared repair. It preserves the old votes in audit metadata and
runs a bounded ordinary autopilot judge. A nonmatch stays available for further C
work; explicit user and valid assembly verdicts are preserved.

If a saved global pointer's address pair appears before an aggregate argument
copy but the target forms it afterward, inspect assignment-valued pointer field
arguments. For a source-owned helper proven to return one nonvolatile global
array on every path, try the direct `array[0].field` argument and capture the
pointer immediately after the call. Preserve the earlier capture and scopes
until the ordinary linked judge verifies simplification. The shared
`constant_pointer_capture_timing` repair proposes this bounded change; it does
not infer or rewrite the callee ABI. Verify aggregate arguments against the
actual callee, since old matched callers can carry misleading prototypes.

Repeated aligned scalar loads through a local byte pointer can schedule differently
when expressed through a candidate-local record view. With `-O2 -g3`, one private
function's halfword load moved before a global pointer store and matched exactly.
The shared inferred-record repair also recognizes implicit byte offsets for unique,
unescaped initialized local byte pointers, retaining its two-field/three-access
minimum. Explicit early scalar captures changed register allocation instead.
Treat the inferred record as a local hypothesis and keep ordinary linked verification.

For byte-counter guards whose promoted index survives into the body, inspect local/global allocation
in `.lreg` and `.greg` dumps. A named 32-bit scratch can capture the byte bound, then receive the
comparison result in a separate statement. This can change which local temporaries reserve return
registers before the counter is allocated. Keep both stores sequenced; a nested double assignment
can accidentally match while violating C90 sequence rules. Redirect an equality scratch only if the
following branch overwrites its old destination before any read. Ordinary linking remains decisive.

If an advisory diff shows a symbolic address disagreement around a masked `%lo` addend,
compare the raw linked words before changing C. Older splits can omit the low annotation;
the display now completes an adjacent pair only when its raw address and register flow
agree with the annotated high. This changes diagnostic scores, never the linked-byte gate.
An exact opcode sequence with different spill offsets still requires a genuine C solution.

When an integer index dies before a local-array address is repeatedly passed to
calls, a union can give those disjoint phases one carrier. Assign the integer member
and later the pointer member before reading each; do not reinterpret the old value.
One measured 257-instruction `-O2 -g3` case improved from a persisted score 70 to 10
with this phase reuse, then matched by capturing a pure unescaped local immediately
before the preceding call. Keep separate C90 statements and debug line boundaries.
Never move the capture across an argument-side write, address escape or read of its
old destination. The shared bounded repair proves local dominance, preserves unrelated
volatile operations, and still requires exact linking. Its first-page replay is a
regression of that same match, not an additional matched function.


For library initializers, consult related local SDK sources before copying assembly
store order into C. A related version is a source lead; verify its record layout,
globals and behavior separately. Typed record field stores can schedule differently
from raw pointer casts. When only the final operands of a chained assignment differ,
try reversing two distinct same-typed ordinary fields: both conversions and the RHS
must remain identical. The bounded `equal_type_field_chain_order` repair preserves
line breaks and requires the ordinary exact judge. In one measured initializer,
manual source/context recovery reached 20 and this first-page proposal reached zero;
constant reuse and broad declaration changes had not recovered it.

For a signed `value<0?-value-width:value` normalization whose arithmetic matches
but copy/negation registers differ, try the bounded `scalar_normalization_scope`
repair. A saved scalar, a branch-local dead reset of the original, and a one-shot
scope can prevent old GCC CSE from replacing the saved value with its source.
The original is restored before the next statement. Split negation, unscoped
copies and simple if/else arms are not equivalent allocation evidence. Only the
ordinary linked-byte judge can accept the candidate; advisory symbol/literal
renderings can remain different after the linked bytes are exact.

A consumed scalar can carry a later constant store inside a one-shot scope:
`do { scratch=48; out->field=scratch; } while(0);`. In one measured initializer,
plain lifetime splitting selected the right register but reordered the store;
unscoped reuse retained the wrong register. The scoped reuse matched both. The
bounded `constant_store_scope` repair requires a visible, same-type ordinary
scratch whose old value is dead on every CFG path, and a replaced local dead after
its store. It preserves one execution and rejects observable/escaped locals and
macro aliases. Review operand/conversion errors separately before treating a small
score as only allocation; the ordinary exact judge still decides.

When a literal indirect store is scheduled after later independent stores or mask
work, try putting that single store in `do { ...; }while(0);`. One controlled KMC
`-O2 -g3` replay recovered exact linked bytes this way; a plain block and a scalar
global declaration alone did not. Preserve the original lvalue and declarations.
The shared `constant_memory_store_scope` repair offers a bounded version.

Do not repair allocation by inserting cancellation pairs such as `x++; x--;`.
They can overflow signed values or leave valid pointer bounds before returning to
an apparently unchanged value. A lower score does not make them valid. The native
wrapper excludes its self-assignment insertion family; review old saved drafts
separately, and use corrective candidate adoption when a cleaner draft scores worse.


### Discarded byte increments

A cached signed-byte `value+1` store before a call can lower differently from a standalone
unsigned-byte increment. In one controlled KMC2.7.2/O2/g3 case, both discarded prefix/postfix
unsigned spellings recovered exact linked bytes from score70; a destination cast alone did not.
Keep the cached signed value unchanged if it is used later. Require the same byte address and no
intervening call, write, pointer mutation or volatile access; never introduce signed-byte increment
overflow or change a used expression result. The shared `unsigned_byte_increment` repair proves
its supported narrow CFG/read shape and otherwise skips. The normal judge remains authoritative;
one case does not justify a broad increment rewrite.

### Bounds-loop branch destinations

A low-score goto can skip the outer bounds test. Check the backedge destination
and its comparison in the delay slot before retaining that C. For a pure named
comparison, an explicit outer return guard paired with function-only
`-fno-thread-jumps` recovered one exact KMC 2.7.2/O2 case; either source or profile
alone missed. Shared normal/deep permuter preflight tries a tiny supported paired
set even when the source alone scores worse. Preserve source layout. A `beq` versus
`beql` mismatch changes delay-slot execution, so only linked bytes establish a
match. See `docs/COMPILER_MATCHING_LEARNINGS.md` for controlled scores and failed
experiments; this is not a recommendation to change the project's global flags.

### Isolated spill slots and register chains

Equal frames with one differing spill store/load pair need a local storage/lifetime
hypothesis before global frame padding. `mapped_spill_storage` uses the actual object's
C-line association to select the assignment's local; `mapped_register_lifetime` uses
a small consistent register chain to try same-type conversion lifetime changes.
A source association is not a register prescription. Stale/missing line information
provides no proposal. Preserve compiler flags and exact checking; the first held-out
sweep did not improve the two motivating residuals, so do not repeat exhausted shapes
without a new source/context/evidence hypothesis.

### Pure address helpers can interact with branch scope and debug lines

With `-O2 -g3`, a real byte-offset pointer accessor may change delay-slot scheduling
without changing an access. One measured scheduler needed three source choices together:
a private inline signed-halfword address helper, braces around existing if/else assignment
arms, and two adjacent calls on one physical line (370 →200 →exact472 linked bytes).
The minimal helper alone did not help. The shared `inline_byte_address` bundle tries a
bounded, typed version of these shapes; it never adds volatile, changes the ABI or
moves an effect. Keep qualifiers backed by actual behavior, preserve verified source
line breaks, and check actual callback signatures before accepting a low-score draft.


### Dead typed pointer captures can need a pair

With KMC2.7.2 `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`, reusing an existing dead typed pointer
for two later independent array-cell captures recovered a 488-instruction exact linked function.
One such change worsened25->40; the pair recovered0. Preserve the existing load/conversion
boundaries and real intervening initialization. Try `pointer_capture_reuse` through local repairs
before spending model turns on register renaming. Its bounded CFG/type checks reject live storage,
escapes, qualifiers, calls and unknown/duplicating macros. This is one measured case, not a general
allocation rule, and a linked match still needs a complete image rebuild before publication.

When signatures disagree, inspect the selected definitions at both ends of the call. A stale
parameter forwarded only to another unused parameter is not evidence of a real argument. Use
the actual formal type (`void *` and a byte pointer are not compatible function parameter
types), with an explicit conversion where needed. Reverify any corrected existing caller or
callee and the entire image; a corrected declaration is not an additional function match.

### Byte compound-update lifetime

For register-only misses in an ordinary unsigned-byte array update, try loading
one same-type byte local, compound-updating that local, then storing it back:
`value=bytes[i]; value += value >> 1; bytes[i]=value;`. A direct binary-expression
assignment can allocate differently. Keep the original conversion, load/store
order and evaluation boundaries; never cache volatile/atomic reads or move calls.
Two independent sites may need this shape together even when either single
change misses. The shared `loaded_byte_compound` repair tests a small guarded
subset automatically; exact linked bytes remain the acceptance gate.

### Review wrappers and representation copies before allocation searches

A byte-exact `void wrapper(void) { helper(); }` with an unspecified `helper()`
prototype can hide forwarded arguments or a return value. Inspect real callers
and the downstream definition before trusting that signature. Explicit
three-vector forwarding and pointer return preserved three measured 28-byte
wrappers; correcting existing sources does not increase the match count.

For genuine record padding, distinguish representation transport from typed
scalar evaluation. With proven layout/alignment and nonoverlapping objects,
three aligned four-byte `__builtin_memcpy` transfers reproduced an existing
helper exactly; one twelve-byte copy did not. Use byte offsets within the
containing object, not arithmetic through an invented integer array. This
requires audited padding and caller preconditions; do not apply it to arbitrary
uninitialized values. Rebuild the full image after joint edits.

### GP-relative literal diagnostics

`R_MIPS_LITERAL (8)` can come from a compiler-created `.lit4`/`.lit8` pool under
small-data flags such as `-G8`. The object's GP value is not the game's GP.
If the target has no known GP or no corresponding pool-address instruction,
keep the attempt nonmatching. `-G0` is a compiler-profile hypothesis to test
explicitly, not an automatic fix or permission to guess a literal address.
A lower advisory score with an unresolved pool still cannot be accepted.

When a matched empty or constant-zero helper is declared with `(void)` but callers pass
arguments, its exact bytes do not prove that original signature. Unused formals can disappear
entirely from the callee's instructions. Inspect all callers and validate explicit unused
parameters and caller declarations jointly; do not delete argument setup to fit the stub.
Likewise reconcile resource pointer returns before treating integer-shaped drafts as sound.

Compare explicit caller prototypes with selected callee definitions before narrow-argument
searches. A draft's `void(u8,u16)` declaration can disagree with a matched `void(s32,s32)`
definition; neither alone proves the original contract. Check actual argument setup, return
uses and all callers, and validate compatible edits jointly. Do not infer original widths
solely from a lower advisory score or an explicit-looking machine-generated declaration.

### Distinguish incoming width from body narrowing

A callee's signed-halfword shift pair can narrow a full-width incoming parameter
inside its body. A new `s16` prototype may add unwanted truncation at every caller.
In a controlled KMC case, four fully prototyped narrow-formal caller controls
scored 1000/830/440/290; an `s32` incoming formal with the real body narrowing
made all four callers and the callee exact. Audit all declarations and argument
conversions together; do not fall back to unspecified `()` to hide a conflict.
The joint correction preserved the full cartridge, but did not resolve unrelated
legacy indirect-call defects. See `docs/COMPILER_MATCHING_LEARNINGS.md` for scope.

Prototype advice may show `?` for an unresolved typedef while flagging another,
known scalar position. Investigate the known discrepancy without assuming what
`?` means: an unfamiliar object type or return typedef is not resolved by equal
machine widths. Validate the complete caller/callee correction jointly.


For a real unsigned `while (count--)` mismatch, try local family
`unsigned_countdown_guard`. A consumed snapshot (`old=count; count--; if(old)`)
can preserve KMC's original predecrement-value test when the ordinary loop becomes
an end-value comparison. Keep the zero-test decrement and pointer-link types correct;
do not change signedness or invent dead variables to force allocation. One existing
32-byte walker recovered exact bytes this way; it is not a universal loop rule.


Selected empty callees and incoming parameter copies
- Argument-register setup in a conditional delay slot can belong to a later call
  on another path. Do not infer the immediately following callee's arity from it.
  Inspect selected matched C and actual callers first.
- `matched_zero_argument_call` only proposes a caller-owned `void(void)` contract
  when selected matched C explicitly defines an empty `void helper(void)` body.
  It drops only ordinary scalar values or literals, never side-effecting argument
  expressions. Shared declarations, macros and uncertain definitions block it.
- `incoming_parameter_alias` removes a same-type scalar copy only when the incoming
  parameter remains unchanged and its assignment dominates every alias use. The
  composed `zero_argument_parameter_alias` proposal tests both changes together,
  before unrelated lifetime rewrites. Both physical-line-preserving and compact
  forms are bounded hypotheses. The ordinary judge still decides acceptance.
- In a measured KMC case the corrected call alone recovered scheduling, while the
  parameter copies still reversed saved-register allocation. Their composition
  matched without changing compiler flags or removing a required loop reset.
  A relocation-rendering score may remain nonzero in diagnostics: inspect actual
  linked bytes and the ordinary exact verdict; do not weaken the judge.
- This is not permission to rewrite every unused-looking argument. Nonempty
  callees, mixed-arity interfaces and hidden side effects need separate review.

### Promoted input versus a byte local

With KMC GCC 2.7.2, `-G8 -mips3 -mgp32 -mfp32 -O2 -ffast-math
-fno-strength-reduce -g3`, a grounded decoder control improved from score 1100
to 705 when a signed-int input narrowed at entry became a genuinely consumed
`u8` local initialized from that input. A declaration followed by assignment
reproduced 705 too. This is a near-miss improvement, not an exact match.

Keep the public signed-int parameter and caller contract. Replacing the parameter
itself with `u8` can make callers incompatible and is a different experiment.
The bounded `promoted_entry_local` family tries both local forms only for proven
C/32-bit-int builds, a dominating entry conversion and an otherwise stable input.
It skips escapes, hidden includes/conditional types, type inspection, variadics,
C++ and unknown compiler commands. No dummy local or signature widening is needed.
