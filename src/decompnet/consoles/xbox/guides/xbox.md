# Original Xbox (XDK, Visual C++ 7.1)

An Xbox game is an x86 program built with Visual C++ from Microsoft's XDK and packed as an XBE. Registers,
calling conventions, frames and Visual C++'s idioms are as in the x86 guide; this is what differs.

The project
- decompdecomp made the XBE a Windows program at the same addresses (`orig/<version>.exe`, from the user's copy):
  every address you see is the game's own. The XBE's headers are a section of their own at 0x10000, named `XBEH`.
- Visual C++ 7.1 (XDK 5849: 13.10.3077) unless the import said otherwise; flags `/O2 /G6 /GF /Gy /arch:SSE
  /D_XBOX` (tools/configure.py). The project has no XDK headers: declare what a function needs yourself, in its
  file or a header in include/, and never copy XDK headers into the project.
- `/Gy` pads functions to 16 bytes with int3: that padding isn't part of a function.

The kernel and the XDK
- Kernel calls are `call dword ptr [__imp__<Name>]` (the kernel thunk table, named like Windows imports). Declare
  them as imports: `extern "C" __declspec(dllimport) long __stdcall NtClose(void *handle);`.
- The XDK's big libraries (Direct3D, DirectSound, XNet...) are in sections of their own, set aside: they never
  reach you. Its smaller code links into .text next to the game's: if a function looks like the C runtime or XAPI
  (`memcpy`, `XapiInitProcess`, `RtlInitializeCriticalSection` wrappers), say so in a note and release it.
- /arch:SSE: floats are mostly x87 (`fld`, `fcomp` + `fnstsw ax`), some SSE (`movss`, `cvttss2si`); float to int
  calls `__ftol2`. Direct3D calls are often inlined push-buffer writes: write the D3D call, not the stores.

Link-time code generation (LTCG)
Games built with `/GL` and `/LTCG` pass arguments and `this` in eax, esi, edi or ebx, with no standard convention,
and inline across files. In a project made without the LTCG mode (no `"ltcg": true` in `.decomp.wtf.json`), plain
compiles can't match those functions: note "LTCG register convention" and release them. In an LTCG project:
- A unit whose driver `harness/<unit>.drv.cpp` defines `extern "C" __declspec(dllexport) void HarnessEntry(...)` is
  compiled with /GL and linked with /LTCG through that harness; your compile_and_diff goes through it, and the
  linker picks the registers from the driver's calls. Write ordinary C/C++ and compile: a register convention is
  never a reason to release such a function untried. A unit without that (the skeleton's entry is commented out)
  builds plain, which is right for code the game built without LTCG; for a function whose callers pass it
  registers, write the driver first.
- The brief shows the function's convention in the original: registers read before written, stack arguments,
  `ret N`, registers saved, and a few call sites with what they load before the call. When yours differs, fix the
  driver's call or a stand-in so it looks like the original's call site.
- `standins_for(unit)` writes a starting point: stand-ins for what the unit's functions call or read elsewhere,
  with their conventions, a HarnessEntry with each function's call sites as comments to turn into calls, and the
  declarations, and the structs its functions look like members of. Put them in with `edit_file` (`old=""`
  creates a file), then shape them.
  - The driver calls the unit's functions the way the original's call sites do: what eax/esi/edi/ebx/ecx hold
    right before the call, which pushes are constants, what the result is used for.
  - `harness/<unit>.<name>.cpp` holds stand-ins shaped like the real ones (registers read, preserved, returned);
    `<name>.nogl.cpp` compiles without /GL (code the game's link couldn't see into: middleware, XDK libraries).
    A line `// HARNESS-LINK: <flags>` in any of them adds linker flags (`d3d8ltcg.lib`).
  - Functions are found in the link by func_<address> in their names, or by `// FUNCTION: 0x<address> [map symbol]`
    right above the definition (needed for constructors, destructors and operators; no helper between the two).
- Adding a helper function or a call site changes conventions under LTCG. The linker specialises a callee with a
  single call site (arguments left where that caller has them, saves dropped); a second caller (a helper, a
  driver call, a stand-in) can make it standard again, or the reverse. After adding one, check the convention
  evidence again (the brief's against your compile's), and keep the original's number of call sites. When a
  compile's result says the linker gave your function another convention than the original's, that is what
  happened: remove the extra call site (or add the missing one) before touching the function's C.
- Member functions: `this` can arrive in any register (a member function that adds to a field may get it in esi).
  Write a member function when the brief says the function is one, and it still matches. A free function's
  pointer can arrive the same way: prefer a member function when its struct has other member functions
  (`get_class`, when the game has RTTI). A function using two registers at the same offsets is a function of two
  objects (the brief's "Two objects alike"): neither is `this`.
- Unresolved names: the harness link goes on without them (`/FORCE:UNRESOLVED`) and the judge writes the
  original's reference in their place. The brief and compile_and_diff list them by kind:
  - data (a global, a D_<address>, a pool constant): not always harmless. Under LTCG the compiler sees a global's
    definition, type and constness, and an unresolved one can change scheduling around its loads. Define it, with
    its real type, in the unit or a harness file; don't release or wait for it;
  - a callee called with a standard convention (__cdecl, __stdcall, __thiscall): harmless;
  - a callee passed arguments in other registers: the call differs until a definition is in the link. Write a
    stand-in shaped like the original's yourself, in the same claim;
  - a callee the original inlines (no call to it in the target): its body must be in the link;
  - library code (XDK, C runtime): leave it unresolved, declared as its header has it; a /GL stand-in changes the
    calls to it. If one is needed, `<name>.nogl.cpp`.
- Float constants the target loads by address (the diff shows their values): try both the literal (`0.5f`) and an
  `extern const float D_<address>`, and keep the better score; the loads schedule differently and either can win.
  On equal scores prefer the literal. Sometimes the matching form is a folded expression (`1.0f - 1.3f`).
- 100% isn't a match until the references map one to one onto the original's: a name that says an address
  (`D_<address>`, `func_<address>`, a global with `// GLOBAL: 0x<address>` before it) means the original's, and
  floats and strings have its values. The judge says so when they don't.
- `volatile` in a driver or stand-in, to steer the compiler, is forbidden: the harness must reproduce the
  original's call sites and callees faithfully, or its matches are false ones the final link loses.
