# Port: matched C that also builds for a PC

The port keeps one copy of the game's C. A fix is kept only when both builds agree: the console's own compiler
still makes the game's exact bytes, and a current clang building for a 64-bit PC (x86_64 Linux, `PORT` defined)
takes it with no errors and fewer pointer-size warnings than before. `check_for_pc(function, source)` shows the PC's
verdict alone, quickly; `fix_for_pc(function, source)` judges both and keeps it.

What the PC compiler refuses, and the usual fix
- `[undeclared] call to undeclared function 'osFoo'`: C99 and later need a declaration. Add the prototype to the
  header (`edit_context`), with the types the callers use. Matched functions must still match after it.
- `[integers as pointers] incompatible integer to pointer conversion`: a variable, parameter or field holds an
  address but is typed `s32`/`u32`. Give it a pointer type where it's declared; on MIPS a pointer and a 32-bit
  integer load, store and add the same way, so the console bytes rarely change.
- `[pointer size] cast to smaller integer type`: a pointer squeezed into 32 bits, which loses its top half on a
  64-bit PC. Keep it a pointer; do pointer arithmetic on `u8 *` instead of on integers. Casting to a wider integer
  just to silence the warning is not a fix.
- `[assembly] unknown register name '$31' in asm`: MIPS `asm` statements. Write what they do in C behind
  `#ifdef PORT`, keeping the asm in the `#else` branch for the console.
- `[size checks] declared as an array with a negative size`: the C's own size check of a struct that holds
  pointers. A port's struct is bigger: guard the check with `#ifndef PORT`, or check against `sizeof(void *)`.

Keeping the console match
- Change types first: a pointer instead of an integer usually compiles to the same instructions.
- What the console compiler can't take the same way goes in `#ifdef PORT` ... `#else` (the original) `#endif`.
  The console build doesn't define `PORT`.
- Don't reorder statements or change what's computed: the judge compares every byte.
- `uintptr_t` and `<stdint.h>` exist only on the PC side; the console's header has `u32`, `s32` and friends.

When it won't pass after a few tries, `release_port_fix(function, note)` with what you found (which line, what
you tried, why the console bytes changed), so the next try starts from there.
