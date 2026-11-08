# Using your own Watcom compiler

Watcom is available in Setup and the compiler selectors for **32-bit Windows PE and flat DOS LE games**. Support is
experimental: 10.0a C and simple C++ are checked through the real engine. Other DOS formats, full Watcom C++
COMDAT support and automatic Watcom version detection remain planned work. Do not substitute 10.0a for 10.5 just
because a small function matches.

## Try the development copy

**PR prerequisite:** [#139 — build Ghidra's native decompiler on Macs](https://github.com/n-delic/decompdecomp/pull/139)
must land before the Watcom PR. This worktree uses its runtime fix from commit
`41f526ec5618895c9a143cdcefe69ccdddfa24d0`, adapted to this checkout's toolchain interface. Without it,
Apple Silicon imports can find functions but cannot generate C drafts. Xcode Command Line Tools are required
to build the pinned release's own decompiler source. The Watcom changes additionally invalidate old empty disk
drafts and database seeds when the native decompiler is installed or rebuilt; existing projects need no manual
database edits or source replacement.

Use a development checkout with a separate project home and environment:

```sh
cd /path/to/decompdecomp
DECOMP_HOME=/path/to/scratch/app-home \
UV_PROJECT_ENVIRONMENT=/path/to/scratch/venv uv run decomp
```

1. Mount your compiler CD or locate your existing installation. For the tested 10.0a CD, select the `WATCOM`
   folder inside the mounted disc. Keep the disc mounted while compiling.
2. In **Setup → Compilers & tools**, find **Watcom C/C++ (your own copy)**. Add that folder and accept use of your
   own proprietary copy if you agree. Nothing is downloaded or copied from it.
3. Confirm that Setup lists C and/or C++ with the expected version. They are separate choices. Unknown builds
   show an explicit unknown label; compiler bodies, not the identical loader executables, identify a build.
4. Drop your game's `.exe`, or use **Use a file or folder on this computer**. Choose the configured Watcom C or
   C++ compiler explicitly. No Visual C++ download or consent is needed for a configured copy.
5. Import and start work normally. The judge still requires 100% objdiff with relocation and literal checks.
   Helpers should read `guide("watcom")`; Ghidra's default Windows prototype may miss register arguments.

The own-compiler kit is registered by folder, not by ISO filename. An ISO must be mounted first; the app does not
unpack or install it. If the selected folder disappears, remount it at the same path or add the new location in
Setup. The incomplete 64 KiB C body in the earlier 10.5 copy is deliberately not offered as a working compiler.

The Windows PE format is important: an `.exe` extension alone does not distinguish Windows from DOS. Inspect a
local file without importing or exposing its contents:

```sh
uv run python -m decompnet.consoles.pc.watcom --executable /path/to/game.exe
```

The DOS loader accepts little-endian 386/486 LE programs with flat USE32 objects, enumerated/zero pages and
internal fixups. It maps the pages, applies 32-bit absolute/relative fixups, and gives the existing analysis and
judge a PE memory view. Export restores the original LE page layout and loader placeholders, retaining its stub,
tables and overlays byte for byte. The original executable is read-only. DOSBox is not needed for this pipeline.

Selector and 16-bit offset fixups in code now accept explicit external-address OMF targets. The converter verifies
each source site, kind, LE object and full target offset before preserving the original loader placeholder.
Absolute COFF proof symbols let both the judge and exported rebuild reject stale objects or identical literal
placeholders without those verified fixups. Progress reports apply the same check. Function briefs list the
required targets; `guide("watcom")` includes a real 10.0a `#pragma aux __seg` example. A tools update returns only
the earlier automatic DOS set-asides to agents, preserving other assembly decisions and existing source.

Internal segment/group targets and segmented data initializers still need support. OS/2 imported modules,
LE drivers, iterated/compressed pages, far pointers and fixup chains are
rejected explicitly. LX, 16-bit MZ/NE and other DOS extenders need separate loaders. Issue #81 remains partly open.

## Builds, flags and limitations

- Defaults: `-bt=nt -mf -3r -ox` for Windows, `-bt=dos4g -mf -3r -ox` for DOS LE. `search_flags` compares register/stack conventions (`-3r` and `-3s`), not Visual
  C++ switches. Per-file compiler choices and flags work through the normal PC tools.
- The kit recognizes BINNT loaders plus BINB bodies for 10.0a or BINW bodies for 10.5, ignoring case. Both use
  the user's H folder. Known body SHA-256 fingerprints identify the checked builds; IDs also include the language
  and body hash so different copies cannot silently stand in for one another.
- Linux/macOS use pinned wibo. Normal matching and the project's rebuild check do **not** need DOSBox: the rebuild
  writes candidates back into the original program, checks relocations, and compares bytes.
- 10.0a has neither `__int64` nor `long long`; its shared header doesn't invent 64-bit scalar aliases. Drafts using
  such types need rewriting. Machine draft recovery of Watcom register prototypes remains incomplete.
- Flat USE32 OMF segments, public/external symbols, BSS and explicit 32-bit DIR32/REL32 fixups are supported.
  COMDAT, fixup threads, iterated data, unsupported frames and symbol-changing linker directives fail with an
  error. Virtual methods and other C++ code using those records remain planned work.
- Watcom C register symbols and free C++ functions are normalized for judging and real-name changes. Watcom RTTI
  and embedded debug type formats aren't read; the Visual C++ Rich-header compiler survey is not applied to it.
- The real tool runs were on Apple Silicon. Native Windows and Linux runs still need validation. On Windows the
  compiler runner requires existing short (8.3) aliases for source/include paths containing spaces.

Source and headers are read in place through short temporary aliases on Linux/macOS: 10.0a splits long filenames
even when quoted, including response files. Global WCC386/WPP386/INCLUDE settings cannot override the project's
flags. Failed compilation or conversion leaves the previous object untouched. The one maintained converter is
`arch/x86_omf.py`; exports and project tool updates copy it as `tools/omf.py`, with its bytes included in the tools
version. No proprietary compiler is exported.

## What was verified

`projects/demo-watcom/build.py` builds the shared CC0 Demo Quest C source with the user's verified Watcom 10.0a
compiler. Set `DECOMP_TEST_WATCOM_C` to your installation root and have LLVM's `lld-link` on PATH. On macOS/Linux,
the script uses decompdecomp's pinned wibo. It converts the compiler's OMF objects to COFF, then links a homemade
PE test fixture with lld-link; no proprietary linker or compiler files enter the repository. The build writes
`build/demo.json` with symbol addresses for `tests/test_consoles.py`. That suite skips the Watcom cases when the
user's compiler, Ghidra, objdiff, Java, wibo or lld-link is absent. The four console conformance checks import the
demo, inspect a machine draft, reject wrong C, accept exact C and verify the rebuilt game byte for byte. This
fixture checks the compiler and judge; it does not claim to reproduce Watcom's original linker output.

`tests/test_watcom_integration.py` imports the homemade PE with real Ghidra, selects the configured Watcom copy,
builds without an MSVC pack, and runs claims, briefs, wrong/correct attempts, compiler trials, flag search,
polishing, real names, Check again and byte-identical rebuilding through the app's engine. C and C++ are tested
separately. Other tests cover Setup/discovery, an incomplete compiler body, portable tool copies, source/include
paths containing spaces, default family flags, source-split flag preservation and import consent.

`tests/test_pc_linear.py` constructs independent LE fixtures (no game data), including out-of-order pages,
cross-page/negative-source fixups, selectors, zero fill and overlays. Its opt-in C/C++ engine tests import a
homemade LE, reject wrong C, accept matching code with calls/data, polish/rename, reimport and rebuild the LE.
`tests/test_x86_segments.py` checks full offset/segment identity, rejects proof-free identical bytes in progress,
and runs real C/C++ selector matching, wrong-object/literal rejection, byte-identical export and old-project migration.
A user-supplied DOS game was also imported in a scratch home and rebuilt byte-identically; its data stays outside
the repository. This confirms extraction/rebuilding, not that the whole game has been decompiled or its compiler
version established.

The independent judge test compares compiler output against hand-assembled `add eax, edx; ret`: addition matches
100%, subtraction does not. Relocation tests check targets and addends independently of objdiff. A larger probe
contains global references, external calls and a switch. Its normalized C++ object was identical for 10.0a and
10.5, which is evidence for that probe only, not for interchangeability on a game.

Opt-in tests need pinned free tools in an isolated toolchain folder and the local compiler. The PE path below must
be the homemade `demo.c`/`demo.cpp` probe, not a bundled retail executable:

```sh
DECOMPNET_TOOLCHAINS=/path/to/scratch-tools \
DECOMP_TEST_WATCOM=/path/to/WATCOM DECOMP_TEST_WATCOM_C=/path/to/WATCOM \
DECOMP_TEST_WATCOM_PE=/path/to/homemade/C.EXE \
UV_PROJECT_ENVIRONMENT=/path/to/scratch-venv \
  uv run pytest tests/test_pc_linear.py tests/test_watcom_integration.py tests/test_x86_omf.py tests/test_watcom.py -q
```

## Building the homemade PE under DOSBox

DOSBox Staging 0.83.0 ran the original DOS linker successfully on Apple Silicon, avoiding the BINNT linker's
wibo/Rosetta GS-selector failure. The official macOS universal DMG SHA-256 was verified as
`d8a771adfb8010fa6b5f7fb5351abfba659273ad01c89f03675a92bdbdae8167`. It was run from a read-only scratch mount,
with scratch HOME/configuration and a read-only compiler mount. Nothing in the user's installation was changed.

Copy only the homemade sources from `projects/demo-watcom` to a scratch workspace. Mount that as C: and the
compiler root as read-only D: using DOSBox's `mount ... -ro`. For 10.0a:

```text
set WATCOM=D:\
set PATH=D:\BINB;D:\BIN;Z:\
set INCLUDE=D:\H
D:\BINB\WCC386.EXE -bt=nt -mf -3r -ox -fo=demo.obj demo.c
D:\BINB\WASM.EXE -fo=entry.obj entry.asm
D:\BIN\WLINK.EXE @link10.rsp
```

For C++, replace WCC386/demo.c with WPP386/demo.cpp. Linker 10.0 rejects PE OPTION START; the homemade entry.asm
supplies the entry point through its END directive. Both languages linked to 2 KiB PE32 files accepted by the PC
reader. The probe is freestanding, for inspection, and was not run as a Windows application.

For 10.5, tools are in BINW; `link.rsp` uses its supported OPTION START instead. Its working C++ compiler and
linker produced a PE whose 26 code bytes matched independently applied COFF relocations. The supplied 10.5 C
body failed with Loader read error under both DOSBox and wibo; the complete 10.0a C body worked in both.

For headless 0.83.0, use SDL_VIDEODRIVER=dummy, SDL_AUDIODRIVER=dummy, output=texture, a scratch HOME and
`--noprimaryconf --nolocalconf --conf /path/to/scratch.conf`. The trials used 32 MiB RAM, core=normal, process
memory caps and timeouts. Check compiler/linker logs and output files; DOSBox exit code alone isn't a verdict.

## References

- [Open Watcom record definitions](https://github.com/open-watcom/open-watcom-v2/blob/master/bld/watcom/h/pcobj.h)
- [Open Watcom compiler guide](https://open-watcom.github.io/open-watcom-v2-wikidocs/cguide.html)
- [Open Watcom linker guide](https://open-watcom.github.io/open-watcom-v2-wikidocs/lguide.html)
- [DOSBox Staging 0.83.0](https://github.com/dosbox-staging/dosbox-staging/releases/tag/v0.83.0)

The later Open Watcom documentation describes the format family; it isn't proof of a period compiler's exact
behavior. The local, legally obtained copies and homemade probes establish the results above.
