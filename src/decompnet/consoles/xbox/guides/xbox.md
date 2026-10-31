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

Link-time code generation
Games built with `/GL` and `/LTCG` pass arguments and `this` in eax, esi, edi or ebx with no standard convention,
and inline across files. A plain compile can't match such a function: note "LTCG register convention" and release
it. decompdecomp doesn't build those yet (ROADMAP.md).
