# Visual C++ builds for Windows PC games

This page is about Windows PC games only: 32-bit x86 programs built with Microsoft Visual C++. It says which
builds of Visual C++ decompdecomp has, how it picks one for a game, and where the code that Visual C++ makes
changes from one build to the next.

## Why the build matters

A function matches when the C an agent writes compiles to the same bytes as the game. Visual C++ changes the code it
makes from version to version, and sometimes from update to update. The same C, built with two builds, can give
different bytes. So decompdecomp builds each game with the build that made it, or the closest one it has.

A program says which Visual C++ made it in its Rich header: the version, and the build number of `cl.exe`. The
import reads it and picks a compiler:

1. The build the program names, if decompdecomp has it.
2. Otherwise, the closest build of the same version.
3. A game built with a version decompdecomp doesn't have yet (2012 to 2015) imports, but its functions can't match
   until that version comes. Games built with 2017 or newer aren't started.

The Rich header keeps only the build number, not the whole version. Some updates kept the build number of `cl.exe`
but changed the compiler behind it, so the Rich header can't tell them apart:

- Visual C++ 2005 RTM and SP1 are both build 50727. The import takes SP1, which most games of that time used. RTM
  stays available for a file or a game that needs it.
- Visual C++ 6.0 SP5 and SP6 are both build 8804.
- Visual Studio .NET 2002 SP1 kept build 9466 of RTM.

## The builds decompdecomp has

| VS | Visual C++ | `cl.exe` build | Released | Pack |
| :-- | :-- | :-- | :-- | :-- |
| 6.0 | 6.0 (also used by SP1 and SP2) | 12.00.8168 | 1998 | `msvc` |
| 7.0 | Visual Studio .NET 2002 | 13.00.9466 | 2002 | `msvc` |
| 7.1 | Visual Studio .NET 2003 | 13.10.3077 | 2003 | `msvc` |
| 8.0 | 2005 | 14.00.50727.42 | 2005 | `msvc2005` |
| 8.0SP1 | 2005 SP1 | 14.00.50727.762 | 2006 | `msvc2005` |
| 9.0 | 2008 | 15.00.21022.08 | 2007 | `msvc2005` |
| 9.0SP1 | 2008 SP1 | 15.00.30729.01 | 2008 | `msvc2005` |
| 10.0 | 2010 | 16.00.30319.01 | 2010 | `msvc2005` |
| 10.0SP1 | 2010 SP1 | 16.00.40219.01 | 2011 | `msvc2005` |

`src/decompnet/toolchains.toml` pins where each one comes from, with its checksum.

## Where the code changes

Some changes touch nearly every function. Others touch only rare code. A new default touches every function that
uses it: a game built with the old default can't match with the new compiler, and the other way around. These are
the documented changes that matter most for matching.

| Visual C++ | What changed | What it touches |
| :-- | :-- | :-- |
| .NET 2002 (7.0) | A new compiler generation. `/GS` (buffer security checks) is new, but off by default. | Every function |
| 2005 (8.0) | `/GS` and `/GR` are on by default. Named return value optimization (NRVO) at `/O1`, `/O2` and `/Ox`. New inline rules. `/G3` to `/G7` are gone: one "blended" model for all processors. `/fp` replaces `/Op`. | Every function |
| 2005 SP1 | Bug fixes in the code generator. | Rare code |
| 2008 (9.0) | Tuning for newer Intel processors is on by default, and can't be turned off. | Most functions |
| 2010 (10.0) | `/GS` protects more kinds of local buffers. | Functions with local arrays |
| 2012 | x86 code uses SSE2 by default (`/arch:SSE2`), not the x87 floating-point unit. Loops are vectorized automatically. | Every function with `float` or `double` math, and many loops |
| 2013 | More vectorization (loops with min and max, and straight-line code). The compiler tracks value ranges and removes branches it doesn't need. | Loops and code with many branches |
| 2015 | Function-level `static` variables get thread-safe initialization (`/Zc:threadSafeInit`). `delete` calls the sized form (`/Zc:sizedDealloc`). | C++ only: functions with local statics, and code that deletes objects |
| 2015 Update 3 | A new optimizer (SSA) is on by default. It rewrites arithmetic, comparisons and branches in the whole function. | Most functions |
| 2017 15.3 and 15.5 | The optimizer is reworked again: it runs before and after loop optimization, small functions are always inlined, and more branches become conditional moves. | Most functions |

The service packs before 2012 (6.0 SP3 to SP6, .NET 2002 SP1, .NET 2003 SP1) list only bug fixes. The 6.0 Processor
Pack adds intrinsics for SSE, SSE2 and 3DNow!, which code uses only when it asks for them. None of these documents
a change that touches most functions.

## Builds that would help next

These builds have documented changes that touch many functions. A game built with one of them can't match with the
builds decompdecomp has.

| Visual C++ | `cl.exe` build | Why it helps | Source |
| :-- | :-- | :-- | :-- |
| 2013 Update 5 | 18.00.40629 | 2012 and 2013 use SSE2 by default. 2013 also stands in for 2012 until a 2012 source is found. | ojdkbuild/tools_toolchain_vs2013e |
| 2015 Update 3 | 19.00.24210 | The new optimizer. It also covers 2017 15.0, which has the same optimizer. | SincoMao/AptioVBuildEnv (`EWDK1703`) |
| 2017 15.9 | 19.16.27027 | The last 2017 build, with the 15.3 and 15.5 optimizer. | ojdkbuild/tools_toolchain_vs2017bt_1416 |
| 2012 Update 4 | 17.00.61030 | The first SSE2 default. | None found yet. A 2012 release candidate (17.00.50522) exists, but it is unknown if games used. |

If you know a public archive of Visual C++ 2012 (RTM or an update), please
[open an issue](https://github.com/n-delic/decompdecomp/issues).

## Sources

- Visual C++ 2005: [Breaking changes in the Visual C++ 2005 compiler](https://learn.microsoft.com/en-us/previous-versions/visualstudio/visual-studio-2008/ms177253(v=vs.90)),
  [What's new 2003 through 2015](https://learn.microsoft.com/en-us/cpp/porting/visual-cpp-what-s-new-2003-through-2015)
- Visual C++ 2005 SP1: [VC2005 SP1 bug fixes: back end](https://devblogs.microsoft.com/cppblog/vc2005-sp1-bug-fixes-back-end/)
- `/GS`: [the Visual C++ team blog on /GS](https://devblogs.microsoft.com/cppblog/gs/)
- Visual C++ 2012: [SSE2 by default](https://learn.microsoft.com/en-au/windows-hardware/drivers/develop/avoiding-floating-point-errors-in-custom-build-environments),
  [What's new for Visual C++ in Visual Studio 2012](https://learn.microsoft.com/en-us/previous-versions/visualstudio/visual-studio-2012/hh409293(v=vs.110))
- Visual C++ 2013: [Performance with Visual C++ 2013](https://devblogs.microsoft.com/cppblog/performance-with-visual-c-2013/),
  [/arch in Visual Studio 2013](https://learn.microsoft.com/en-us/previous-versions/visualstudio/visual-studio-2013/7t5yh4fd(v=vs.120))
- Visual C++ 2015: [/Zc:threadSafeInit](https://learn.microsoft.com/en-us/cpp/build/reference/zc-threadsafeinit-thread-safe-local-static-initialization),
  [/Zc:sizedDealloc](https://learn.microsoft.com/en-us/cpp/build/reference/zc-sizeddealloc-enable-global-sized-dealloc-functions)
- Visual C++ 2015 Update 3: [Introducing a new, advanced Visual C++ code optimizer](https://devblogs.microsoft.com/cppblog/new-code-optimizer/)
- Visual C++ 2017: [MSVC code optimizer improvements in 15.5 and 15.3](https://devblogs.microsoft.com/cppblog/msvc-code-optimizer-improvements-in-visual-studio-2017-versions-15-5-and-15-3/),
  [What's new for C++ in Visual Studio 2017](https://learn.microsoft.com/en-us/cpp/overview/what-s-new-for-cpp-2017)
- Visual C++ 6.0 updates: [Product updates for Visual Studio 6.0](https://learn.microsoft.com/en-us/previous-versions/aa700916(v=msdn.10))
