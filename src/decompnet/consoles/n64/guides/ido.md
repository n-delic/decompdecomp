# Matching IDO 5.3 / 7.1 (most first-party N64 games)

Usual flags: `-O2 -mips2` (some files `-O1`, `-g`, or `-mips1`). If almost nothing matches, the project
may need a different version (5.3 vs 7.1) or flags: tell the user, they can switch it in the app.

Traits
- Unconditional jumps inside functions are `b label` (`beq $zero, $zero`).
- IDO is sensitive to how code is written on a line. Splitting or joining statements
  (`a = b; c = a;` vs `c = a = b;`) changes the output.
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

## Other SGI comparison profiles

`ido-5.3-cxx` uses a separate old C++ frontend. `ido-4.1`, `ido-5.2`, `ido-6.0` and `mipspro-7.4.4` are explicit comparison profiles; their presence does not establish that a retail N64 game used them. Their checksum-pinned packs run through a bundled IRIX emulator on Linux x86_64. These archives have case-sensitive paths, so this upstream implementation does not offer them on Windows or macOS. The normal linked-byte judge still decides a match. Installing these proprietary packs requires license acceptance.
