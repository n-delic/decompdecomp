# Port: matched C that also builds for a PC

The port keeps one copy of the game's C. A fix is kept only when both builds agree: the console's own compiler
still makes the game's exact bytes, and a current clang building for a 64-bit PC (x86_64 Linux, `PORT` defined)
finds it no worse than before on errors and on pointer-size warnings, and better on one. So a big function is fixed
a group of lines at a time: submit each group that passes, and you keep holding the function until nothing is left. `check_for_pc(function, source)` shows the PC's
verdict alone, quickly; `fix_for_pc(function, source)` judges both and keeps it.

What the PC compiler refuses, and the usual fix
- `[undeclared] call to undeclared function 'osFoo'`: C99 and later need a declaration. Add the prototype to the
  header (`edit_context`), with the types the callers use. Matched functions must still match after it. When that
  alone fixes the function, submit its C unchanged with `fix_for_pc`: the new PC result is what's kept.
- `[integers as pointers] incompatible integer to pointer conversion`: a variable, parameter or field holds an
  address but is typed `s32`/`u32`. Give it a pointer type where it's declared; on MIPS a pointer and a 32-bit
  integer load, store and add the same way, so the console bytes rarely change.
- `[pointer size] cast to smaller integer type`: a pointer squeezed into 32 bits, which loses its top half on a
  64-bit PC. Keep it a pointer; do pointer arithmetic on `u8 *` instead of on integers. Where a value really is
  an integer that holds an address (a display-list word, a hardware register's value), the PC's type for it is
  `uintptr_t`, behind `#ifdef PORT`; a cast to `s32`/`u32` is never a fix.
- `[assembly] unknown register name '$31' in asm`: MIPS `asm` statements. Write what they do in C behind
  `#ifdef PORT`, keeping the asm in the `#else` branch for the console.
- `[size checks] declared as an array with a negative size`: the C's own size check of a struct that holds
  pointers. A port's struct is bigger: guard the check with `#ifndef PORT`, or check against `sizeof(void *)`.

Keeping the console match
- Change types first: a pointer instead of an integer usually compiles to the same instructions.
- When it doesn't (the old compiler orders `offset + base` and `base + offset` differently, or picks other
  registers once a variable is a pointer), don't spend tries on reordering: keep the original lines for the console
  and write the pointer-typed ones for the PC:

      #ifdef PORT
          rec = (u8 *)track->records + track->count * 48;
      #else
          rec = (s32)track->records + track->count * 48;   /* the matching original */
      #endif

  Wrap as few lines as do it; a whole function in two versions is fine when its body is all pointer arithmetic.
- The same for MIPS `asm`, gcc-only builtins and anything else the console compiler can't take the same way. The
  console build doesn't define `PORT`.
- Don't reorder statements or change what's computed: the judge compares every byte.
- `uintptr_t` and `<stdint.h>` exist only on the PC side; the console's header has `u32`, `s32` and friends.

Patterns that span functions
- Display lists. A `Gfx` command is two 32-bit words on the console and two pointer-sized words in libultraship,
  so a word that holds an address loses half of it in a `u32`. In the shared header, give the PC its own `Gfx`
  (`#ifdef PORT` `#include <stdint.h>` and `typedef struct { uintptr_t w0, w1; } Gfx;` `#else` the console's
  `#endif`); then a command's address word is set from the pointer, `g->w1 = (uintptr_t)D_8009EB40;`, and a list
  advances by `sizeof(Gfx)` or `dl++`, never by 8. Code that writes `*(s32 *)(cmd + 4)` and adds 8 is building
  commands by hand: its `#ifdef PORT` branch uses the `Gfx` fields.
- gcc's old varargs (`__builtin_next_arg`, walking the argument area as `s32 *`). The PC branch uses `<stdarg.h>`:
  `va_list ap; va_start(ap, fmt);`, and the function it passes the arguments to takes a `va_list` on the PC, so its
  prototype in the header gets an `#ifdef PORT` version too. Fix the callee first (claim it by name with
  `claim_port_fix(function)`), then its callers; the next Compile for PC shows what's left of the family.

When it won't pass after a few tries, `release_port_fix(function, note)` with what you found (which line, what
you tried, why the console bytes changed), so the next try starts from there.
