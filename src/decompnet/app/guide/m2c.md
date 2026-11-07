# Reading the m2c draft

m2c translates the assembly to C mechanically. It is a starting point, never a match. Its control flow and
arithmetic are usually right; its types and names usually wrong. Start from it and fix those.

- The draft is made from the shared header's declarations the function uses, and made again when they change.
  Worked out a struct or a prototype? `redraft(function, context)` runs m2c again with your declarations and
  returns the new draft (recorded nowhere). Add what helped to the header with edit_context.
- A method of a C++ class (the class recovery found it in a vtable, or it stores one) is shown as
  `Class::function(...)`: m2c's `arg0` is `this`, and the class lists the fields its functions use, named
  as m2c names them (`unk4` is at +0x4).

- `?` types: m2c couldn't tell. Replace with s32/u8/a struct pointer as the instructions suggest
  (see the `mips` guide's "Types from instructions").
- `arg0`, `var_v0`, `temp_s0`, `sp10`: placeholder names. `spXX` is a stack local at offset XX.
- `*(s32 *)(arg0 + 0x1C)`: a struct field. Collect the offsets a pointer is used with and define a
  struct (see the `structs` guide), then write `p->field`.
- `M2C_ERROR(...)` or `goto`s everywhere: control flow m2c couldn't structure; rebuild it by hand from
  the assembly.
- Calls declared `/* extern */` with `?` arguments: m2c guessed the signature from register use. Check
  it against other callers and add a real prototype to context.h.
- `saved_reg_*`: a register preserved for the caller; usually nothing to write in C.
- m2c inlines expressions aggressively. The original often had temporaries; put them back when the
  register allocation differs.

Machine drafts preserve exact call addresses: an interior `function+offset` is not a C callee.
The initial draft search also tries a bounded optional explicit backedge for eligible do/while
loops; the raw m2c draft remains available. Shared repair search can stage an unsigned row counter
and reuse disjoint scalar storage with conversion boundaries preserved. These are compiler-matching
hypotheses, not evidence of the original source. Loops with unsafe transfers or ambiguous
initialization stay unchanged, and only the ordinary compiler/judge can establish a match.
Partial m2c output and `M2C_ERROR` placeholders are reported as generation failures.
Draft regeneration tries bounded private callback/frame repairs after compilation fails;
only the ordinary compiler and exact judge can establish a match. Missing delay slots
or targets outside the recorded function need boundary review, never invented instructions.

Autopilot retains raw m2c output and can judge up to three additional typed-store
alternatives: replace a CFG-dead embedded scalar assignment with a destination-typed
coordinate temporary, fold consecutive narrow field updates while retaining their
intermediate cast, or combine the two. Declared record types and scalar bounds are
required; no missing layout is invented. These candidates also reach shared local
and deep repair search. They are optional compiler-matching trials, not unconditional
cleanup of m2c output. A narrow temporary and a wide temporary need not allocate alike.
