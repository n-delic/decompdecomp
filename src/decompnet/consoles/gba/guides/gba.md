# Matching Game Boy Advance code

The ARM7TDMI runs ARM code (4-byte instructions) and Thumb code (2-byte). GBA games keep most code in Thumb: the
cartridge is 16 bits wide, so Thumb's smaller code runs faster from it. Start-up code, interrupt handlers and some
IWRAM routines are ARM. A function is one or the other; its listing says which.

Calling convention: r0-r3 arguments, r0 return; r4-r11 callee-saved; r12 (ip) scratch; r13 sp, r14 lr, r15 pc.
Thumb uses mostly r0-r7. A Thumb function starts `push {..., lr}` or, when it is a leaf, with no prologue; it
returns with `pop {..., pc}`, or `pop {rN}` + `bx rN` when it must interwork with ARM code.

Reading the code
- Literal pools: a constant or address that doesn't fit an immediate is loaded PC-relative (`ldr r0, [pc, #n]`).
  The words sit after the function, in first-use order. A `bl` is two halfwords; its destination is in the listing.
- `add r0, r1, r2, lsl #2` is `r1 + r2 * 4` (array indexing). `ldrsb`/`ldrsh` are signed, `ldrb`/`ldrh` unsigned:
  they say the field's type. `lsl #24` + `asr #24` sign-extends an s8.
- The ARM7 has no divide instruction and no FPU: `/`, `%` and float arithmetic call the runtime's helpers
  (`__divsi3`, `__modsi3`, `__mulsf3`...). Write the plain operator; the runtime supplies the call.
- A BIOS call is `swi #n`. No C compiler emits `swi`, so such a stub is hand-written assembly: decompdecomp sets
  it aside. The project's headers wrap the BIOS functions by name; use the wrapper.
- A branch into the middle of a symbol, or code that returns without having saved the registers it restores, is a
  piece of a function, not one: split or join it rather than writing C for it.

Matching agbcc
- Declaration order and first use decide register allocation. Reorder before rewriting.
- Narrow types matter: every `u8`/`s16` local or parameter gets truncation shifts after arithmetic; `int` doesn't.
- Loop shape (`for`, `while`, `do {} while`), `if (a) { if (b) }` against `if (a && b)`, and early returns against
  one exit all change the block layout.
- Casts on subexpressions change where truncation happens. Try one more or one fewer temporary.
- `register u32 x asm("r4")` pins a variable to a register. Use it only when natural C gets close and nothing else
  works, as pret's decomps do.

Project conventions (pret style): `gSomething` for globals, `sSomething` for statics, `sub_08001234` for what
nobody named yet. Keep the names the assembly shows, or rename the address first with `rename_symbol`.
