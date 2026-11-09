# Matching PlayStation (PS1) code

CPU: the R3000A, MIPS I, little-endian. Things that look odd at first:
- Load delay slot: the instruction after a load can't use the loaded value, so you'll see `nop`s or reordering
  right after loads. Branch delay slots exist too; there's no branch-likely, no 64-bit registers, no FPU (a
  `float` becomes calls to `__addsf3`, `__mulsf3`...).
- `mult`/`div` put their results in HI/LO (`mflo`, `mfhi`). A division is followed by checks for divide by zero
  (`break 7`) and overflow (`break 6`): Sony's assembler added them, and the project's build reproduces that.
  Write a plain `/` or `%`.
- `$gp`-relative small data (-G8, sometimes -G4): small globals are read with one instruction,
  `lw $v0, %gp_rel(sym)($gp)`, instead of `lui` + `lw`. Declare such globals in context.h *without* `extern`
  (`s32 D_8001B278;`): the compiler then treats them as small data. With `extern` it builds the full address and
  the function won't match. Arrays and structs bigger than the limit are never `$gp`-relative.

Compilers: PSY-Q-era GCC 2.5.7, 2.6.0, 2.6.3, 2.7.2 (PSX and Cygnus CDK variants), 2.8.0, 2.8.1,
EGCS 2.91.66 and GCC 2.95.2, with Sony's assembler ASPSX. The supported versions depend on the host; check
Compilers & tools. PSY-Q 4.3, 4.4, 4.5 and 4.6 correspond to different GCC/EGCS versions, not one late compiler. The project builds each function the way the community's PS1 decomps do: GCC, then maspsx (which does what
ASPSX did: the `nop`s it put after loads, its division checks, how it expanded `li`), then GNU as. Versions differ in
register allocation and scheduling: if right-looking C never matches, try_compiler(function, "gcc-2.6.3-psx") or
"gcc-2.8.1-psx" tests another without recording anything, and suggest(...) the one that matches.

Libraries: PSY-Q's libgte, libgpu, libgs, libspu, libcd, libetc, libapi and libc were linked as prebuilt objects
with the game. Their functions are worth naming (rename_symbol) from what they do, and worth matching only when the
game's own code calls them a lot.

GTE (coprocessor 2, the geometry engine): `mtc2`/`mfc2`/`ctc2`/`cfc2`, `lwc2`/`swc2` and commands like `RTPS` or
`NCLIP` often come from PSY-Q's GTE macros (`gte_ldv0`, `gte_rtps`...). Their presence alone is not a reason
to set aside a function: use the supported fixed intrinsics below. A BIOS call stub
(`li t2, 0xB0; jr t2; li t1, n`) is a different case: it may genuinely be handwritten assembly.

Typical patterns: `jtbl_` jump tables in .rodata for dense switches; overlays loaded at the same address (each a
segment of its own); `D_800xxxxx` data and `func_800xxxxx` names for what nobody named yet.

## Fixed GTE intrinsics

GCC 2.6.3, 2.7.2 and 2.8.1 PS1 builds supply a small trusted GTE prelude. Use named C calls such as
`gte_ldv0(p)`, `gte_rtps()` and `gte_stsxy(out)`: submitted inline assembly and arbitrary instruction words
remain forbidden. The supported subset includes the rotation/translation matrix setters, selected vector
and matrix-column transfers, screen/depth/flag outputs, RTPS/RTPT, rotation, clipping, depth averaging and
square commands. Unsupported operations still need an implementation; their presence does not prove that
the original function was handwritten assembly. Check the available definitions in `consoles/ps1/gte.py`.
The `_b` command forms omit the two leading wait nops; `_wait` supplies one. These retain period SDK scheduling
conventions, and each C candidate must still match every linked byte.

Old SDK reference objects may contain DMPSX command placeholders instead of final GTE instructions. Expand
only recognised named operations when preparing local reference objects; never count a masked object shape
as a C match. Whole-source-file context is useful for declarations and identity, but extracted C must pass
the same judge with the actual function's compiler settings and original overlay data.

## Recovering matches from local source without model calls

If the user supplies reference source, compile candidates in batches before spending time on individual
functions. Start with the largest unmatched functions whose identity is supported by verified neighbours
or call references. Use an isolated project copy; only import candidates that pass the ordinary linked-byte
judge, and regression-check existing matches after shared changes.

### Keep the PS1 build context

Use each source revision with its own headers and PS1 build defines. A later port can retain familiar function
names while changing types, helpers and control flow. Dreamcast renderer or audio declarations are not missing
PS1 declarations: exclude the wrong platform branch rather than inventing replacement types.

When an old compiler prints a garbled undeclared name, a modern compiler's syntax-only diagnostics can identify
it. Use that compiler for diagnostics only; the period compiler still decides the match. Restore declarations
from the appropriate source headers and confirm load/store widths against the game. Check preprocessor output:
SDK register macros can accidentally expand struct fields named `v0` or `v1`. Repair the preprocessing context
instead of adding fictitious fields to make the error disappear.

### Sweep helper differences across source files

A helper mismatch can affect many otherwise correct functions:

- A texture lookup may have been a string-to-CRC call followed by a CRC lookup in the PS1 build. Verify both
  call destinations before applying that expansion to related candidates.
- A debug allocator may take size, source filename and line number, while a later wrapper takes only size.
  Recover each call's arguments from the target and check the filename bytes in the correct segment. A filename
  pointer may be kept in a saved register across calls; a short scan immediately before the call can miss it.
- A port may compile out a disc-availability check. Restoring the evidenced call can also restore register
  allocation and stack size; compiler-flag searches cannot compensate for missing control flow.

Preserve compiler-recognised names such as `strcpy` when assigning address aliases. Period GCC can inline a
constant-string copy; renaming it to an arbitrary address symbol can turn it into a call. Likewise, replacing
its string literal with an external pointer can hide the length and change generated instructions. Keep the
literal visible and let the judge verify its linked data references. Do not count matching relocation-masked
instructions as a verified match.

### Use the diff to separate layout changes from missing behaviour

A same-sized candidate can still combine several independent source-revision differences. Resolve them in
small steps and keep the comparison from each step:

- Missing calls or branches indicate missing behaviour, not just register allocation. A port may replace a
  disc check with a constant, suppress a pickup sound lookup, or add a state reset absent from the retail
  executable. Consult the available revisions and call targets before changing those statements.
- If several accesses through one structure differ by the same number of bytes, compare their field positions
  against earlier accesses that still agree. Correct the candidate-local layout at the first affected field;
  do not shift the entire structure or patch every instruction's immediate independently. If the field's
  meaning is unknown, label the recovered space as unknown rather than inventing an original field name.
- Rendering helpers that still call a later platform's primitive-submission function need the PS1 ordering
  table macro from the corresponding SDK headers. Preserve its bitfield form: a manually rewritten mask can
  change register allocation even when it appears equivalent.

An SDK object signature can identify an entry hidden inside a larger detected function. Treat its relocation
mask as identity and boundary evidence only. Compile recovered C independently, compare every linked byte,
and test the SDK compiler version on that function when an otherwise identical wrapper only differs in
instruction scheduling. Keep any successful compiler override confined to the verified range and recheck
all existing matches before importing it.

### Repair only what the evidence supports

Text enums can differ between source revisions. Propose candidate-local corrections using the target's
immediates and check every linked byte; do not shift all text IDs or rewrite shared enums on one observation.
For function boundaries, combine a return and delay slot, the next prologue, and callers or callback-table
references. A compiler size mismatch alone does not justify splitting a function. Overlay references must be
read from the function's own segment, even when multiple overlays share a RAM address.

### Check merged entries before rewriting correct source

A candidate that ends cleanly before its target does may already describe a whole function. For a bulk pass:

1. Compile each source candidate with its own declarations and the target's compiler settings. Record its
   byte length alongside the target's length, rather than treating the difference as missing C statements.
2. Inspect the candidate-length offset in the target. A completed return and delay slot followed by a fresh
   stack prologue is a boundary clue. Check callers, callback tables, or independently identified source
   neighbours too; an internal return or unreachable block is not sufficient evidence.
3. Split only an unmatched entry in the isolated copy. Retain the trailing bytes as a separate function;
   do not discard them or remove them from coverage. Run the ordinary judge on the complete prefix.
4. Try the newly separated neighbour against its own source. A useful split can recover both functions,
   but the neighbour stays unmatched until its own linked bytes pass.
5. Record split operations before source submissions so the verified batch can be replayed through the app.
   Recheck all existing matches after replaying it.

If the boundaries are sound and just a few instructions differ, inspect register lifetimes and pointer
qualification before making broad source changes. Period GCC may keep the address of a global in a saved
register when it is accessed through a local const pointer. An equivalent candidate can therefore restore a
missing move without changing the compiler flags. Treat this as a hypothesis to test against the complete
linked function, not a rule to add const pointers everywhere. Record such adaptations separately from the
reference source, and do not patch machine instructions to make the comparison pass.

Record unsuccessful candidates separately from verified matches. Report matched bytes as well as function
counts, and retain the original project snapshot so a bulk import remains reviewable.
