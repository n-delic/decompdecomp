# Matching PlayStation 2 code

CPU: the Emotion Engine (R5900), MIPS III-like with 128-bit registers.
- `lq`/`sq` move 128 bits; MMI instructions (`pcpyld`, `paddw`, `pextlw`...) come from inlined
  memcpy/memset or vector code.
- `mult rd, rs, rt` (three operands) and `madd` exist; `mflo` still appears.
- The FPU is single precision only: `double` arithmetic becomes calls (`__adddf3`, `__muldf3`): if you
  see those, a double was involved (or a literal without `f`).
- 64-bit integer ops (`daddu`, `dsll32`, `ld`/`sd`) mean `long`/`long long` (ee-gcc: `long` is 64-bit).
  GCC writes register moves as `daddu rd, rs, $zero` (a 64-bit move), older code as `addu`/`or`.
- COP2 is VU0 in macro mode (`vadd.xyzw`, `qmfc2`, `qmtc2`): from the SDK's inline VU0 helpers; use the
  project's macros/functions.
- Float compares: `c.lt.s` + `bc1t`/`bc1f`; float to int: `cvt.w.s` + `mfc1`.

Compilers: Sony's ee-gcc 2.9x (most games), Metrowerks for PS2, or SN Systems' compiler; the project
decides per file. Games started from scratch get one picked from the code itself (CodeWarrior leaves `nop` in
the delay slot of nearly every `jr $ra`, GCC fills it; CodeWarrior 2.x moves registers without `daddu`), then
among the GCCs by compiling drafts with each. Notes in the program (".comment") can come from one library only.
If nothing matches although the C is right, the compiler may be another one: try_compiler(function, compiler,
flags) tests one without recording anything (try several), then suggest(compiler, flags, function, ranges, source)
the one that matches: it's checked again, and refused if it doesn't match. Games can mix compilers (GTA San Andreas: EE-GCC
2.95.3, and CodeWarrior 3.0.1 -O3 for some ranges: CodeWarrior's frame keeps $ra at 0($sp) with locals above it,
GCC's at the top; at -O3 CodeWarrior fills delay slots too). Then pass ranges="0x...-0x..." to suggest it for those
address ranges only; the brief says when a function is in such a range. compiler_map() shows where the code looks
built by another compiler than today's, and the brief says so for the function you hold; compiler_map(survey=true)
has the judge build agents' unmatched C with each compiler the project uses (in the background), and turns what it
confirms into suggestions. A one-function range proves nothing: neighbouring functions decide. A range you suggest
is tested on its neighbours before it applies (a range check, compiler_map() lists them); a neighbour may come to
you as a task, with a note: match it, then try_compiler it with the range's compiler. Matching with one compiler and
not the other is the answer either way.
- CodeWarrior: small data is `-sdatathreshold 8`, not `-G8`; it gives each function its own section and
  loves `lui`/`addiu` pairs over `$gp` when the threshold is 0.
- EE-GCC 3.2 loads a switch's jump table with `addiu %lo(jtbl)` then `lw 0(reg)`; 2.9x/2.96 use
  `lw %lo(jtbl)(reg)`. That alone tells the two apart. C++ is common: old GCC 2.9x mangling (`update__7CPlayerf`) and vtable thunks.
Games started from scratch take C++ too: source that doesn't compile as C is compiled as C++, and its names are given
back to the game's, so `void CPed::func_00123456(int x)` is judged as func_00123456. A method's `this` is its first
argument ($a0), as in C. Put classes in the shared header inside `#ifdef __cplusplus` ... `#endif`: the functions
already matched as C must keep compiling as C (a header edit that breaks one is undone). Constructors and destructors
can't be named by address: write them as ordinary methods (`CPed *CPed::func_0012AB30()`) that set the vtable.
`get_class` has the layout of the class a method belongs to (fields with their offsets and types, base class,
virtual functions by slot), found from the game's own constructors: start a class from it.
Small data is `$gp`-relative (-G8 typical), so declare externs with their real sizes.

Libraries: Sony's libgraph, libdma, libpad, libcdvd, libkernl, libsd and libvu0 are linked as
libraries; their prototypes are in the project's headers.
