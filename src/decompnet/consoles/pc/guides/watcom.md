# Watcom Windows and DOS LE games

Choose the exact Watcom copy and language in Setup and import. 10.0a and 10.5 are different builds, even when a
small function compiles identically. The judge must establish a match. Flat 32-bit DOS LE and Windows PE are
supported; 16-bit MZ/NE, LX and other DOS extenders are not. DOS imports use `-bt=dos4g`, Windows `-bt=nt`.
LE selectors and 16-bit offsets need symbolic OMF fixups, not literal placeholders or guessed flat pointers.
`get_function` lists each site's object and target. Use named external addresses (`D_XXXXXXXX`, `func_XXXXXXXX`)
so the compiler's fixup can be verified against the original object and full offset. A selector may name any
address in that same LE object; a 16-bit offset must identify the exact target, including bits above bit 15.

For example, Watcom's auxiliary calling convention can emit a verified data selector:

```c
extern char D_00050000;
unsigned short load_data_selector(void);
#pragma aux load_data_selector = 0x66 0xb8 __seg D_00050000 value [ax];
```

This example emits `mov ax, seg D_00050000`; calling it is inlined by Watcom. Prefer the game's C declaration
and Watcom's far/based-pointer features where they emit the required fixup. Do not replace the selector with zero:
the word in the file is a loader placeholder. Incorrect/missing fixups fail compilation with an explanation even
if those placeholder bytes would match. Internal segment/group targets, segmented data initializers, far-pointer
fixups and fixup threads remain unsupported; ask for support rather than hardcoding their bytes.

The default flags are `-bt=nt -mf -3r -ox`: flat 32-bit Windows, 386 register convention, optimized. `-3s` uses stack
arguments. Use `try_compiler` for a different copy or language, and `search_flags` to compare register/stack
conventions. File flags override the project defaults. Do not add Visual C++ switches such as `/Oy-`.

The register convention passes words in EAX, EDX, EBX and ECX, in that order. Inspect the assembly and call sites:
Ghidra's default Windows prototype may infer stack arguments instead. Write `__watcall` for the default register
convention, or use Watcom's `#pragma aux` when the original uses a custom convention. Keep the function's real
parameter types and return type. Don't reproduce instructions with inline assembly just to satisfy the judge.

Define functions using their `func_XXXXXXXX` names and globals using `D_XXXXXXXX`; the build normalizes Watcom's
symbol decoration before objdiff. Renamed free C and C++ functions are supported too. C imports start with `.c`
files, C++ imports with `.cpp`. Use the selected language; a C++ compiler is not a replacement for a C compiler.

Watcom 10.0a has no `__int64` or `long long`. The shared header does not fake `s64`/`u64`. Model pairs of 32-bit
values as the assembly does. Machine drafts using 64-bit scalars need rewriting before compilation.

The OMF converter supports flat USE32 segments and explicit 32-bit absolute/relative relocations. It rejects
unsupported records instead of dropping information. COMDATs, fixup threads and iterated data remain planned
work, so some C++ methods, virtual tables and large initializers cannot compile through this build yet. Read the
compiler/conversion error and report the unsupported record; never remove a relocation or weaken a match.

The app reads compilers and headers in the folder chosen in Setup. A mounted CD must stay mounted at that path.
Exported projects contain the build tools, not the proprietary compiler; configure them with your own copy.
