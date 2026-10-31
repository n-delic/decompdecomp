# Matching GBA code built with agbcc

agbcc is pret's rebuild of the GCC 2.95-era compiler most Game Boy Advance games used. The same release has
`old_agbcc` for some early games and `agbcc_arm` for ARM code. Typical flags are `-mthumb-interwork -O2`; the
project sets them, and every function is judged with them.

What moves the output
- Local declaration order and first use change register allocation. Reorder before rewriting.
- Narrow types: every `u8`/`s16` local or parameter gets truncation shifts after arithmetic; `int` locals don't.
  Match the shifts in the target by choosing types.
- Loop shape: `for`, `while` and `do {} while` produce different block layouts.
- `if (a) { if (b) }` against `if (a && b)`, early returns against one exit.
- Temporaries: introduce or remove one; reuse a variable for two values.
- Casts like `(u16)`/`(s32)` on subexpressions change when truncation happens.
- `register u32 x asm("r4")` pins a variable to a register; pret projects use it sparingly where nothing else works.
  Prefer natural code first.

Project conventions (pret style): `gSomething` globals, `sSomething` statics, `Task_*` callbacks, `FAKEMATCH` and
`NONMATCHING` markers for hacks. Look at how matched files in the project write similar code before inventing a
trick.

Calls into the BIOS use `swi`; the project wraps them (`CpuSet`, `LZ77UnCompVram`...): use the wrappers.
