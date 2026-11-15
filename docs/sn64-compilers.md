# SN64 compiler integration

The N64 compiler picker now includes `sn-n64-281` and `sn-n64-272-0001` alongside KMC and IDO.
They can be selected for a project or tested per function through `try_compiler` and `search_flags`.
Existing compiler choices are preserved. A compiler-map suggestion still needs matching evidence.

## Original tools, ordinary judge

The pipeline is cppn64 → cc1n64 → asn64 → PCSX-Redux PsyQ converter → big-endian ELF.
The normal linker places that ELF at the target address and compares bytes. The adapter does not
substitute guessed instruction sequences or accept a disassembly similarity score as a match.

- **2.8.1** bundles its own preprocessor and ASN64 2.78 from the pinned SN64 release.
- **2.7.2 build 0001** uses that release's preprocessor and ASN64 2.56.
- Mixing the newer compiler with the older assembler fails on `%hi`/`%lo` expressions.
- The preserved Windows 2.8.1 binary is a specific build; it is not claimed to reproduce every
  other SN64 2.8.1 build, including reconstructed Linux compilers.

Tool archives and all converter source dependencies have SHA-256 pins. The proprietary programs
run through wibo on macOS/Linux and the WSL path on Windows. The free converter is built once
with a C++20 compiler and zlib headers. Windows/WSL compilation is covered structurally by the
shared staging path; the real end-to-end validation for this addition was on Apple Silicon macOS.

The converter's source and dependency licenses stay in its installation directory. No original game
code, data or compiler executable is added to this repository.

## Setup

Accept the relevant compiler's license in **Compilers & tools** and install it. Install the
free PsyQ converter too. On macOS, install Xcode Command Line Tools first; Linux/WSL needs
`g++` with C++20 support and `zlib1g-dev`. An older 2.8.1 compiler-only pack needs reinstalling
to obtain its preprocessor and assembler. Missing tools produce a reinstall instruction rather
than downloading proprietary binaries from the matching loop.

## Bounded local comparison

A private copy of a European N64 project supplied 32 functions, 12–240 instructions long,
spread across its address ranges: 16 already matched controls and 16 unfinished candidates.
Every compiler received the same existing C and context. These sources had been developed under
KMC, so this is a compiler probe, not an unbiased model or compiler benchmark.

| Profile at its default optimisation | Compiled | Matched controls | New matches among unfinished candidates |
| --- | ---: | ---: | ---: |
| KMC GCC 2.7.2 | 32/32 | 16/16 | 0/16 |
| SN64 GCC 2.8.1 + ASN64 2.78 | 32/32 | 14/16 | 2/16 |
| SN64 GCC 2.7.2 build 0001 + ASN64 2.56 | 32/32 | 3/16 | 0/16 |

Both new matches also failed KMC at `-O0`, `-O1`, `-O2` and `-O3`, and passed the ordinary
attempt judge under 2.8.1. This is useful evidence for those functions, not proof of the compiler
for the whole game. Small functions can match more than one compiler; SDK code may differ from
engine code. Live project settings and sources were not modified.

The two new matches were committed to the private database using single-function compiler ranges,
and both were included as C in an exported mixed rebuild. That rebuilt the original analysis image
byte for byte after restoring the previously documented startup BSS addresses **in the scratch
export only**. This validates the SN adapter/export path; it does not claim to fix the existing
project's incomplete function discovery or automatic BSS detection. The copied project also
contained a non-VR4300 `pref` decoding: export now preserves such annotated words verbatim,
with a regression test, rather than asking the assembler to accept an out-of-ISA mnemonic.

Validation ran the normal `try_compiler` API, source preprocessing, integer arithmetic, external
calls/data relocations, floating-point constants, the pinned native converter build, GCC flag
search/permuter-family selection, read-only driver staging for WSL, and the exported rebuild.
The application was not reinstalled and no live compiler ranges were applied.

## Additional profiles

| Profile | Pipeline | Validation in this change |
| --- | --- | --- |
| `gcc-2.8.1-pm` | Matching GCC fork and paired assembler | Real C compilation and linked byte checks on macOS |
| `gcc-egcs-2.91.66` | EGCS 1.1.2 and paired binutils | Real C compilation and linked byte checks on macOS |
| `sn-n64-272-970404` | Reconstructed native GCC, modern-asn64, GNU assembler | Native Apple Silicon source build, real C compilation and linked byte checks |
| `sn-n64-272-0004` | Original SN compiler/assembler, preserved-pack conversion | Real C compilation and linked byte checks on macOS |
| `sn-n64-272-0006` | Original SN compiler/assembler, preserved-pack conversion | Real C compilation and linked byte checks on macOS |

Downloads and the reconstructed source commit are checksum pinned. The source
build adds only a host assertion-reporting helper required by Apple's runtime;
it does not patch target code generation. PM and EGCS Intel binaries require
Rosetta on Apple Silicon. Linux packs and Windows through WSL are configured,
but physical-host verification remains outstanding. These profiles enter the
shared compiler picker, flag comparisons, GCC drafts and permuter selection;
imported projects continue using their own build scripts. No live app or game
project was changed during validation.

After explicit license acceptance, builds 0004 and 0006 were installed and
passed real arithmetic, call/data relocation, floating-point and branch
compilation checks, including exact linked byte comparisons. Build 0006's
preprocessor requires an absolute include directory under wibo; the driver
normalizes that path, with a regression test for relative sandbox inputs.

## Additional comparison profiles

The picker also offers original EGCS 1.1.2-4, GCC 4.4.0, IDO 4.1/5.2/6.0,
IDO 5.3's separate C++ frontend and MIPSpro 7.4.4. These downloads contain
Linux x86 executables. Windows uses WSL; macOS can use the optional
[local Linux VM runner](linux-compiler-vm.md). Native macOS builds of the additional
SGI emulator and original GNU releases remain a gap. Existing native IDO 5.3/7.1 and reconstructed EGCS remain available
on macOS. A listed compiler is not evidence that a retail game used it.

Original EGCS uses the pinned 32-bit runtime. GCC 4.4 includes pinned Debian
GMP/MPFR host libraries, without requiring system package installation. Both
profiles passed arithmetic, calls/data relocations, floating point and branches
in an isolated x86 Linux container on an Apple Silicon VM; the ordinary linked
byte judge passed arithmetic and call/data relocation checks. i386 translation
used a smaller QEMU guest reservation to stay inside the unchanged compiler
memory cap. Physical x86 Linux and WSL remain untested.

The SGI profiles execute their checksum-pinned bundled IRIX emulator, preserving
32-bit big-endian output. IDO 4.1 emits ECOFF: the bundled converter's ELF writer
is adapted to big-endian metadata without changing code bytes or relocation
semantics. IDO 5.3 C++ uses the separate `CC` frontend. SGI installation requires
explicit acceptance; real compiler execution is pending that acceptance.

The SGI profiles and GCC 4.4 are marked experimental and omitted from routine
installed-profile comparisons. Select one explicitly for a project or function
with `try_compiler`; a project's explicitly configured profile/ranges still
participate in bulk comparisons. This avoids expanding every routine search
with speculative toolchains. Imported projects keep their own build scripts.

## GCC code generation with original SN assembly

**GCC 2.8.1 + SN ASN64 2.56** (`gcc-2.8.1-pm-sn-as`) is a separate C matching
profile. It compiles to assembly with the pinned PM GCC build, then uses the
preserved SN64 0001 pack's ASN64 2.56 and the pinned PsyQ-to-ELF converter. It
reuses the existing SN adapter's metadata cleanup and CRLF conversion; generated
instructions are not patched. The ordinary linker still requires exact bytes.

Install it in **Compilers & tools** after accepting the proprietary assembler's
license. It becomes available in compiler selection and installed-compiler
comparisons. Native macOS and Linux use the pinned tools; Windows uses the
existing WSL path. Physical Linux/Windows validation remains outstanding.

This pairing can reproduce instruction scheduling and macro encodings that
neither original profile reproduces alone. It does not establish which compiler
release a retail game used. Prefer function-level settings when only individual
functions have been verified; do not infer whole-game settings from a few matches.

Instruction diffs now show `[bytes ...]` when two instructions have identical
printed aliases but different relocation-masked encodings. For example, MIPS
`addu` and `daddu` may both print as `move`. These differences remain non-matches;
relocated address fields are still ignored in this diagnostic comparison.
