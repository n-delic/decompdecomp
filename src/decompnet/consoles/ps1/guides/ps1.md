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

Compilers: PSY-Q's GCC (2.6.3 for 1995-1996 games, 2.7.2 for most, 2.8.1 for late ones), with Sony's assembler
ASPSX. The project builds each function the way the community's PS1 decomps do: GCC, then maspsx (which does what
ASPSX did: the `nop`s it put after loads, its division checks, how it expanded `li`), then GNU as. Versions differ in
register allocation and scheduling: if right-looking C never matches, try_compiler(function, "gcc-2.6.3-psx") or
"gcc-2.8.1-psx" tests another without recording anything, and suggest(...) the one that matches.

Libraries: PSY-Q's libgte, libgpu, libgs, libspu, libcd, libetc, libapi and libc were linked as prebuilt objects
with the game. Their functions are worth naming (rename_symbol) from what they do, and worth matching only when the
game's own code calls them a lot.

GTE (coprocessor 2, the geometry engine): `mtc2`/`mfc2`/`ctc2`/`cfc2`, `lwc2`/`swc2` and commands like `RTPS` or
`NCLIP` come from PSY-Q's GTE macros (`gte_ldv0`, `gte_rtps`...), inline assembly: a function made of them is set
aside as assembly. So is a BIOS call stub (`li t2, 0xB0; jr t2; li t1, n`).

Typical patterns: `jtbl_` jump tables in .rodata for dense switches; overlays loaded at the same address (each a
segment of its own); `D_800xxxxx` data and `func_800xxxxx` names for what nobody named yet.
