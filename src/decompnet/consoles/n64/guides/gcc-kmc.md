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

For a register-only table-index store, try `pointer_index_scope` when the source already
has a block-local slot pointer. Splitting its pointer and index into typed locals inside
a one-shot scope recovered one 52-instruction KMC `-O2 -g3` case exactly. The split alone
made that case worse: test the combined shape before discarding this lifetime hypothesis.
The rule is bounded and retains the normal exact judge; it is not a general register fix.
