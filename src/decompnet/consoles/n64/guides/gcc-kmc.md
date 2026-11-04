# Matching KMC GCC 2.7.2 (many third-party N64 games)

Flags are usually `-O2 -mips3 -G0` (some games `-O1` or `-O3`; ask the user to change the project's
compiler settings if nothing matches at all).

Debug information can also affect the generated instructions. On measured KMC builds, `-g` or `-g3`
changed scheduling and branch delay slots even when the C logic stayed the same. Try those as controlled
flag variants when the instruction shape is close. A win on one function does not establish the flags
for the rest of the game; check neighbouring functions before changing a range.

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

## Work from the first structural difference

Keep the compiler, flags, function address, and shared declarations fixed while testing a C change.
Compare the first differing instruction before chasing the overall score. A displaced branch later in
the function can be a consequence of one earlier instruction, rather than a separate control-flow
problem. Check, in order:

1. Is the control-flow graph right: same branches, calls, shared tails, and return paths?
2. Does the stack frame have the same size, saved registers, and spill slots?
3. Are values live across the same blocks, especially across calls and at branch joins?
4. Do loads, extensions, and comparisons use the same type and signedness?
5. Only then tune expression order, declarations, and scheduling-sensitive source layout.

Keep a best candidate and a structurally different candidate. Record the flags, first mismatch, and
what each experiment changed; do not spend repeated compiles on the same source and flags. A better
score is a useful clue, but only an exact linked-byte result is a match.

## Register lifetimes and shared tails

KMC's allocator can change registers far beyond the line you edited. In measured large functions,
moving a declaration or reusing a local changed the register map across hundreds of instructions.
When one value dies before another is born, trying the same local for both can reproduce the ROM's
register reuse. Conversely, two simultaneously live values need separate locals even if they have
the same type. Check each path through a branch, not just the straight-line path.

A shared epilogue or error tail is particularly sensitive to lifetime. If two branches carry
different pointers to the same tail, the compiler may keep them in a common register, spill one,
or construct a phi-like join depending on the C block structure. Try an explicit shared tail or
equivalent nested blocks, and preserve all side effects and return values. Scoping a temporary to
one branch can help, but changing the apparent control flow just to improve a score can hide a
wrong decompilation.

When a narrow value is extended more than once in the target, look at where the original value
remains live. Reusing an already extended `int` may remove an instruction the ROM has; introducing
a fresh narrow temporary may restore it. Pointer fields and global declarations need the correct
types for the same reason: one incorrect type can alter loads, shifts, and branches throughout a
large function.

## Source layout is another variable

KMC builds with debug flags can be sensitive to physical line grouping. Source line breaks changed
instruction scheduling in measured cases, including delay slots. Once the algorithm, types, and
register lifetimes are close, try grouping or separating adjacent statements while leaving their
semantics unchanged. Keep this as a late, controlled experiment: line layout cannot repair a
wrong call, frame, or branch. Avoid `__LINE__`, line directives, or opaque macros as matching tricks;
the source should remain a readable account of the function.

## Validate the result

An exact match should compile with the project's selected KMC profile and match after linking at
the function's real address. Confirm that the candidate has defined C behaviour and that any
shared header or prototype change does not break neighbouring functions. The matching source is
one possible explanation of the ROM bytes; it does not prove the original developer used the same
names, spelling, or line breaks.
