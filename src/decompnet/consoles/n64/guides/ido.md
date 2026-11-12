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

## Early SGI comparison profile

`ido-4.1` is an explicit comparison profile with a checksum-pinned community pack. IDO 5.2, IDO 5.3 C++, IDO 6.0 and MIPSpro 7.4.4 are also available as profiles when you add your own compiler folder in Setup. Those four need a Linux x86_64 host and the IRIX emulator files in the folder; decompdecomp downloads none of their compiler files. IDO 5.3 C++ needs both `usr/lib/driver` and `usr/lib/CC` in that folder. Setup labels builds it cannot verify as user selected. The normal linked-byte judge decides a match. A profile's presence does not establish retail N64 use.
