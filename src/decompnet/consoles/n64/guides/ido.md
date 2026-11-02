# Matching IDO 5.3 / 7.1 (most first-party N64 games)

Usual flags: `-O2 -mips2` (some files `-O1`, `-g`, or `-mips1`). If almost nothing matches, the project
may need a different version (5.3 vs 7.1) or flags: tell the user, they can switch it in the app.

## Near-match checklist

1. **Confirm context:** check ABI, prototypes, original types and same-unit helpers before broad
   body permutations. Keep compiler, flags, declarations and symbol addresses fixed for comparisons.
2. **Too few instructions:** inspect missing logic and shared branch tails. Counts alone do not
   identify the cause; do not copy KMC GCC lifetime recipes without a separately judged experiment.
3. **Right structure, wrong registers:** try a focused declaration, first-assignment or temporary
   lifetime change. Save separate candidates so its effect is distinguishable from a flag change.
4. **A plateau:** compare frame/spill and call/branch structure, retaining a small useful alternative
   alongside the lowest-score candidate. Check current-input failed history before repeating work.
5. **Preserve layout:** retain the original line layout; test formatting separately under the
   controlled procedure below. The observed layout-sensitive example does not prove that newlines
   alone changed scheduling, or that KMC GCC shares the behavior.

Keep per-function linked-byte/data acceptance and the complete-image check separate.

Traits
- Unconditional jumps inside functions are `b label` (`beq $zero, $zero`).
- Expression structure can change output: `a = b; c = a;` and `c = a = b;` are
  different expressions. Test this separately from changes to line breaks alone.
- It reuses registers in a predictable order; extra temporaries often *don't* help, while reordering
  statements or changing where a variable is first assigned does.
- Common idioms: `if (x) { } else { }` with empty blocks, `do { ... } while (0);` around a sequence,
  re-reading a global instead of keeping it in a local, `*(&x)`.

Floats
- IDO keeps float constants in .rodata (`lwc1 $f4, %lo(D_...)`) unless they're simple: writing `1.0f`
  vs `1.0` (double) matters.

Argument home slots
- IDO stores `$a0-$a3` to the caller's frame (`sw $a0, 0x18($sp)`) when their address is taken or at
  -O0/-g. Spills like that tell you a parameter is used after a call.

Static functions
- `static` functions may be called with a different convention only in the same file; for matching a
  single function it rarely matters, but keep the name the ROM uses.

Source line layout (observed; mechanism unconfirmed)
- Preserve a candidate's line breaks before trying automatic formatting or one-line storage.
  A private IDO 5.3 same-source comparison matched with preserved whitespace (score 0),
  while replacing every whitespace run with one space scored 120 and changed a call/load
  ordering. This establishes layout sensitivity in that example, not a newline-only cause.
  The broader pass added 22 matches, but header/global-declaration fixes also changed between
  passes, so those 22 cannot all be attributed to formatting. Full-image acceptance is separate.
- Source locations may influence compiler decisions, but instruction scheduling has not been
  established as the cause here. Extraction, preprocessing and macro effects must be ruled out.
- For a controlled check, keep the compiler executable, flags, headers, macros, symbol addresses
  and linker setup fixed. Save both inputs; change only newlines between ordinary C tokens.
  Do not move preprocessor directives, continue `//` comments, or change `__LINE__` uses.
  Compare preprocessed token sequences and linked instruction bytes, not whole object files
  whose debug records can differ. Repeat both builds to check determinism.
- Keep formatting variants as separate attempts and rejudge after formatting matched C.
  A per-function match must still pass the complete image/cartridge rebuild. Never change the
  target bytes or judge to accommodate a formatting variant.

## Additional SGI comparison profiles

`ido-5.3-cxx` uses the separate old C++ frontend. `ido-4.1`, `ido-5.2`,
`ido-6.0` and `mipspro-7.4.4` are experimental comparisons, not evidence of
which compiler a retail game used. Their pinned packs run through their bundled
IRIX emulator on Linux x86_64, Windows/WSL or the optional macOS Linux VM.
They do not run natively on macOS; the VM uses its own Python for the adapter.
IDO 4.1's ECOFF output is converted to big-endian ELF before the same byte judge.

These experimental profiles are omitted from routine installed-profile searches.
Use `try_compiler` explicitly with existing C and context. A configured compiler
or range remains in comparisons. Check nontrivial functions before suggesting a
range. Do not infer compiler identity from a trivial arithmetic match.


## Check ABI and context before long permutations

For an existing record returned by value, prefer a real C structure return over a guessed explicit
result pointer. IDO chooses the hidden-pointer ABI and copy schedule. Preserve SDK record types,
command macros and typed pointer locals inside inline helpers when available. These are hypotheses,
not automatic equivalence proofs: judge each candidate with its exact compiler and flags.

The app preflights up to four installed flag profiles before a long ROM search, caching actual inputs.
Exact-winning changes respect the project's automatic compiler setting; similarity-only improvements
remain suggestions. Missing global addresses need relocation evidence before random rewriting. A
function match can still have a non-contiguous constant-pool export blocker; verify the whole image.
See `docs/COMPILER_MATCHING_LEARNINGS.md` for the controlled IDO 5.3 regression and neutral reader experiments.

After a source repair
- Recheck relevant optimization profiles before further mutations: corrected SDK bodies can still need `-O1` or `-O3` instead of `-O2`. Deep search repeats its bounded cached flag preflight after saved local/random improvements; only exact results can apply automatically, respecting automatic-compiler settings.
- With authorized local source, configure its function mapping and declarations. Deep search tries that typed template before permutations and preserves original line layout. Verify cleanup branches against retail; reference revisions can differ.
- Equality operand order and unsigned integer product order can affect instruction scheduling. The local repair families try bounded simple-read variants; volatile accesses, calls and floating-point reassociation are excluded.

Runtime and static-call evidence
- Small signed 64-bit modulo and double-to-unsigned-64 targets can need `-O1 -mips3 -32`, even while surrounding files use MIPS II. Check real 64-bit argument/return widths. Instruction-based preflight hypotheses remain bounded and require the normal exact judge.
- Recorded SDK-signature identity prioritizes `-O1` before wider optimization probes; a name alone is insufficient evidence.
- When target HI/LO relocations prove an interior code address, reference that entry directly instead of retaining a byte-pointer base-plus-offset expression that emits extra arithmetic. Candidate-local declarations do not establish a new function boundary.
- Caller-saved temporaries live across a call can indicate compiler-specialized static-helper clobbers. Inspect the original translation unit and argument registers; adding helper bodies to an isolated candidate is not a proven remedy.

Original object definitions
- If a diff has repeated extra LUI address loads around global-field stores, compare the original translation unit's definitions with the candidate's extern declarations. Source-local definition/layout knowledge can affect code generation; the mechanism is not uniquely established.
- Select only authorized original objects in Local source preparation, using `reference_symbol @ relative/file.c`. The app tries the preserved definitions before the version without them. Do not invent initializers, alignment or addresses to force a match; normal data-placement/export and complete-image checks remain necessary.
- Numeric branch selection preserves source line numbers before function extraction. For unsupported preprocessing or unknown compiler-specific macros, supply explicit numeric overrides or a prepared subset.

If only unresolved runtime-helper call operands differ, confirm the helper ABI at the target call sites before
permuting. An explicit correctly typed helper call can resolve a relocation while retaining the exact instruction
sequence. Preserve original SDK unions, record types, macros and locals. Nearby verified `-O1` profiles are useful
trial priorities for SDK routines, but only the current function's byte judge can confirm a candidate.

A target-supported math alias must also have its correct float/double prototype; implicit-int returns change the
calling convention. If only an allocation immediate differs, compare the ROM's requested extent with reference
sizeof before changing any types. A different allocation extent does not prove a missing field. Prepared-source
improvements merit bounded compiler-profile probes with that source's private header before further permutation.

SDK I/O near-matches may need `-O1` with original `register` locals and byte-aligned response records. Keep the
original volatile MMIO access shape: replacing a proven numeric register address with an address-of-symbol or
scalar-symbol expression can change IDO 5.3's address setup/register selection. Only use addresses proven by the
target; rejudge linked bytes rather than symbolic diff spelling. Inspect retail polling masks, error constants
and recovery paths when an SDK revision differs. Changing optimization alone does not establish compatibility.

## Original helper context before more permutations

An incoming `f12` together with a live integer/pointer in `a0` can indicate an original static-helper
calling convention that an exposed ordinary-C signature does not reproduce. Inspect the caller's argument
setup and local helper context first; `mtc1` defining `f12` is not incoming-float evidence. Register and
stack slots do not directly count C parameters, especially with wide or aggregate arguments.

Use prepared-source investigation evidence to compare compiled calls, field offsets and branch/exit
shapes. Similarity supports a source lead; it does not prove its name or justify automatic retail edits.
After repeated register-only misses under unchanged source/context/profile inputs, prefer a targeted
prototype/type or declaration-lifetime experiment. Exact judging still decides every candidate.


For an internal helper, compare read-before-write temporary registers with the caller’s setup.
Two incoming temporaries set at a direct call can indicate whole-unit register specialization.
Inspect the original same-unit helper and its callers before broad body permutation. This evidence
does not authorize assigned-register C or a guessed calling-convention patch.

Reuse a nearby exact function’s private type context as a bounded hypothesis when types and calls
overlap. Keep the original source line layout and judge each function separately, including its data.

Preserve original object identity as well as type: a typed view of a large application
object can retain IDO's address construction where byte-array arithmetic folds into a
new base symbol. Check the actual SDK macro before declaring a helper function: an
indexed-block macro may forward an extra data pointer, and a matrix macro may take an
object's address. Compare loop expansion versus helper inlining only as separately
judged hypotheses. Retail switch tables can differ from archived source revisions.

A small source helper may return a predicate around another function's result. Preserve its real
body and range checks rather than declaring the underlying callee Boolean. Mask-word signedness
can change loop lowering even with the same field width. With only two load-order differences,
try focused scheduling/declaration hypotheses; with only an extra restore/return suffix, inspect
adjacent original padding for an unreachable epilogue. Neither finding relaxes byte/data checks.


### Let CPU source trials handle evidenced context differences

Mapped-source preparation can compare actual helper prototypes, preserve calls seen in the target,
and privately reuse exact neighbours' named records anchored by shared calls or object addresses.
For aligned MIPS operands with unambiguous compiler line associations, it tries a small consistent
positive record gap (at least two field mappings), comparison macros/literals and explicit array bounds.
Stores map to the assignment's destination, not a similarly named saved-state field on its right.
Retail jump-table labels and structurally identical arm clones are bounded hypotheses, never a source
of arbitrary missing code. Missing line/table/type evidence means the corresponding trial is skipped.

CPU replay of known `-O2` pre-match inputs recovered an exact record-gap trial from score 40 and an
exact 16-to-18 limit trial from score 50. These are replay validation, not additional matched coverage.
Enable automatic exact-bundle rechecks in Local source preparation to save freshly verified private
source/header/profile bundles unattended. Normal claim, stale-input, linked-data and export checks
still apply; approximate scores do not change compiler settings. Existing register/stack/declaration
families remain useful for near misses that these structural repairs do not solve.

## Preserve real context before changing C

An actual private getter can allocate differently from an equivalent macro.
Source preparation can try its supplied function body and a bounded layout with
helper prototypes before the caller and definitions after it. Every result still
needs the byte/data judge and an exportable entry.

Internal `.align` directives can depend on the entry's position in its original
translation unit. The verified predecessor-context path accepts at most two real,
contiguous, already-matched predecessors at their original relative positions;
it rejudges every body and exports only the selected compiler entry. Preserve
source line breaks too. Never invent dummy functions, padding or assembly to
simulate placement. A full original-image rebuild remains necessary.
