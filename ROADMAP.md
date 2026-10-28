# Roadmap

What's planned for decompdecomp, roughly in order, and why. Every item keeps the rule that matters most: a match is
byte-identical after linking (ROM mode) or objdiff 100% (fork mode). Nothing here accepts code that only does the
same thing.

Every console gets every feature it can: one pipeline, ROM mode and project mode alike. A "—" in the table below is
a gap to close, each with its item here (CONTRIBUTING.md, "Every console gets every feature").

Want to work on one? Open an issue first, so two people don't build the same thing. CONTRIBUTING.md has the rest.

## What works where

Each feature and the platforms it covers today. A — is a gap: pick one up (the notes under the table say where to
start). Keep this table true when a feature ships or reaches another platform. Each column's code is its console's
folder, `src/decompnet/consoles/<id>/`; a new console adds a column (CONTRIBUTING.md, "Consoles").

| Feature | N64 | PS1 | PS2 | PSP | GameCube · Wii | Windows PC | DS | Public decomps |
| :-- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Status | recommended | beta | beta | beta | beta | beta | beta | the project's own |
| Start from your own copy | ✓ | ✓ | ✓ | ✓ unencrypted or decrypted program and modules (ISO, CSO, CHD) | ✓ | ✓ | ✓ | fork by address |
| Judge | link · compare | link · compare | link · compare | link · compare | objdiff | objdiff | objdiff | objdiff |
| Machine drafts (m2c, Ghidra for x86) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ Ghidra | — | by architecture |
| Autopilot: free matches from drafts | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ exact drafts of tiny shapes; Ghidra's made compilable | — | — |
| Drafts follow the header, made in the background; `redraft` | ✓ | ✓ | ✓ | ✓ | — | ✓ Ghidra's | — | — |
| C++ methods drafted as `Class::method` | ✓ | ✓ | ✓ | ✓ | — | ✓ (RTTI, and classes from the vtables constructors store) | — | — |
| Twins: free matches from identical code | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Permuter on near misses | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Hand-written assembly set aside | ✓ | ✓ | ✓ | ✓ | ✓ | Visual C++'s exception-handling stubs | BIOS call stubs, pieces of functions | MIPS, PowerPC, ARM's BIOS stubs and pieces of functions |
| Pieces of functions joined to their function | — | — | — | — | — | ✓ (`scratch.merge_pieces`) | ✓ (`recover.merge_pieces`) | — |
| Agents' verdict "no C makes this" (3 agents of 2 models: set aside, red in the map) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Check again (and before helpers start, once after each update) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Kernel stubs set aside and named | — | set aside (BIOS calls) | ✓ | set aside, named by library and NID | — | ✓ import thunks | — | — |
| Function-end reports (`report_boundary`) | ✓ | ✓ | ✓ | ✓ | — | ✓ (agents agree, the rebuild checks) | — | — |
| Compiler ranges and suggestions | ✓ | ✓ | ✓ | ✓ | from scratch: per source file (`try_compiler`, Settings); else the project's build | per source file (`try_compiler`, Settings) | per source file (`try_compiler`, Settings) | the project's build |
| Flag search (`search_flags`) | ✓ | ✓ | ✓ | ✓ | the project's build | per source file (/Oy-, /O1, /Ob1) | the project's build | the project's build |
| Compiler map and the judge's survey | ✓ | ✓ | ✓ | ✓ | — | ✓ per source file, Rich header | — | — |
| Range checks: a range applies after its neighbours confirm it | ✓ | ✓ | ✓ | ✓ | — | ✓ over source files | — | — |
| C++ classes from the code (`get_class`) | ✓ | ✓ | ✓ | ✓ | — | ✓ (named by RTTI where the program has it) | — | — |
| Struct layouts from loads and stores (`guess_struct`) | ✓ | ✓ | ✓ | ✓ | — | ✓ | — | — |
| The game's own debug symbols: names, types, prototypes | — | — | ✓ symbol table, STABS, DWARF 1; the compiler of each object file | ✓ symbol table, DWARF 1 | — | ✓ PDB (Visual C++ 6.0's PDB 2.0, MSF 7.00), linker map, COFF symbols | — | — |
| Twins across your games | ✓ | ✓ | ✓ | ✓ | — | ✓ (between PC projects) | — | — |
| Shared knowledge (two models agree) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Tidy each source file once it's polished (shared types in one header, names, dead declarations) | — | — | — | — | ✓ | ✓ | ✓ | ✓ |
| Polish matched code (still matching) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Real names for the game's symbols (`rename_symbol`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | dtk and ds-decomp projects |
| Save and restore (`progress/`, Open a saved project) | ✓ | ✓ | ✓ | ✓ | the project's git; best attempts saved | the project's git; best attempts saved | the project's git; best attempts saved | the project's git; best attempts saved |
| Saved README preserves notes, with completion bar and function map | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reimport: a new split, matched work kept (Settings) | ✓ | ✓ | ✓ | ✓ | — | ✓ (Ghidra again, the newest rules) | — | — |
| Rebuildable export | ✓ | ✓ | ✓ | ✓ | the project | the project (`ninja rebuild`) | the project | the project |
| Progress bars for foreground jobs | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Rooms: decompile together | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Where to start on a gap:
- **PowerPC (GameCube, Wii) and x86 (PC)**: class recovery and the compiler map work on a project's objects in fork
  mode, not on a ROM; the readers are the part to write (CodeWarrior's vtable pointer is often not at offset 0). x86
  has its reader (`arch/x86_fields.py`): PC classes and struct layouts come from it (`Console.classes`,
  `Console.structs`); a PowerPC reader would give GameCube and Wii the same through those parts.
- **Windows PC**: autopilot's Ghidra drafts compile, but rarely match beyond the tiny shapes exact drafts cover:
  Ghidra drafts a delinked object without its callees' prototypes, so a method call loses `this` and a callee's
  arguments are guessed (a draft fed the project's headers, through a Ghidra script that applies them, is the next
  step). Only games built with Visual C++ 6.0 to 2010 can match so far: Newer
  Windows games, below, is the plan for 2012 to 2015 and 64-bit. A class's constructors are one real name
  (`CFoo::CFoo`), so of two at two addresses (a copy constructor) only one can be written as a constructor: names.py
  would need the mangled signature in "names". A game's own DLLs come along when its folder or .exe is given by
  path; a dropped .exe comes alone (a browser doesn't hand over the folder), a project made before can't take its
  DLLs in yet (Reimport analyses the programs a project has), a project restored or cloned asks only for the .exe (`ForkProject.needs` names
  one file: the DLLs go in `orig/` by hand, as its README says), and a game whose .exe can't start (Visual C++
  2017 and newer) can't start from its DLLs either.
- **Windows PC debug symbols** (`consoles/pc/debuginfo.py`): Ghidra's function boundaries stay Ghidra's, though a
  PDB gives each function's size; CodeView embedded in the program (NB09, NB11: Visual C++ 4-5 with /PDB:NONE) and
  Visual C++ 4's 16-bit type records aren't read; a class's methods aren't written into its struct (C++ calls a
  method only through a declaration in the class), and optimised code's register locals (2005's S_LOCAL ranges)
  aren't listed. A game's PDB has to be beside its program, or dropped with it, at import: a project made before
  can't take one in yet.
- **Function-end reports** cut MIPS functions (`engine._cut`) and, in project mode, a console's own
  (`Console.cut_function`: PC's functions.json). DS and GameCube projects need theirs.
- **Tidying a ROM project**: its C is the database's, one function at a time, with one shared header per segment
  (`edit_context`), so there's no file to tidy (`app/readable.py`, `_untidy`). The header is the place to start: its
  types and names, then the functions' C that uses them, each still matching.
- **Struct layouts** read MIPS and x86 loads and stores (`arch/mips_fields.py`, `arch/x86_fields.py`); another
  processor adds its reader as its `Arch.field_accesses`, and structs.py does the rest. Still open on MIPS: the PS2's
  fifth to eighth arguments (t0-t3), and a pointer into the middle of a struct handed to a callee (`&this->pos`). On
  x86: an element of a global array reached as `[index + address]` (every address the code names is a global of its
  own, `D_<address>`, so the array's fields past the first aren't the element's type), edx's __fastcall argument,
  and the sign of a field the code only or's and tests.
- **Twins across games** and **restoring a save** work on ROM projects (`app/twins.py`, `app/saves.py`). In project
  mode a project's matched C is its git repository; twins across games pair two projects' target objects
  (`Twins._across_projects`, PC so far: GameCube, Wii and DS projects of two games would work the same way but
  weren't tried); what's missing is opening a save's best attempts in a new copy of the project.

## Recently done

- **PSP.** A UMD image (read in place, also as a CSO) or the game's program, with its title and product code from
  PARAM.SFO. A PRX is placed at 0x08804000 the way the PSP loads it, then split by splat like any MIPS program; its
  import stubs are set aside and get the system functions' names (`sceIoOpen`) from the open-source PSP SDK's NID
  list, 2,491 functions in 91 libraries (on a PSP game with debug symbols it names 268 of its 274 stubs, all as the
  game's own symbols do; a NID it doesn't know keeps `<library>_<NID>`). The disc's other PRX modules come with it,
  each a segment laid out after the main program (that game: 28 modules, 9,508 more functions to decompile, 36,002
  more stubs set aside), their functions matching with CodeWarrior 3.0.1 too. The game's own debug symbols come with
  it: the symbol table and
  CodeWarrior's DWARF 1 (source files, prototypes, types, also for CodeWarrior PS2 games). Diffs read the Allegrex's
  VFPU and its own instructions through rabbitizer. Compilers: CodeWarrior for PSP 3.0.1 (11 builds), the PSP SDK's
  GCC 3.3.3 (1.3.1 and 1.7.1) and SN's pspsnc (known by its code, which moves registers with `or`: the demo built with
  it imports, matches at -O2 -G8 and rebuilds byte for byte). On a PSP game with debug symbols: 2,465 functions, 2,458
  named, CodeWarrior 3.0.1 matching its functions byte for byte. Every row of the table was then tried on the PSP
  demo and that game: autopilot matched 251 of the game's 1,968 open functions from drafts and twins 19 more, and its
  export with those 270 in C rebuilds byte for byte, from a fresh clone too. What that turned up is fixed for every
  console: the permuter now reads autopilot's drafts, get_class reads GCC 3's and SNC's vtables, SNC's C++ names
  match, a save keeps what was set aside, the export keeps switch cases in their function and links branches into
  other functions. Small data is read from each program's code, not its gp value (every module's info has one): that
  game keeps none, and the compiler test's drafts of 60 of its functions match 23 with it off against 12. Its export
  with all 28 modules (47,975 functions, 491 in C) rebuilds byte for byte in two minutes, from a fresh clone in four:
  the assembly between C functions is assembled a run at a time (1,216 objects; one per function kept ld busy for
  over 40 minutes), and 8-byte data as words (GNU as moved a module's doubles).
- **Autopilot on Windows PC.** Autopilot didn't run on PC: its free matches come from drafts that compile, and
  Ghidra's didn't (undefined4, in_ECX, a method's `this` dropped). Now it tries, for each function, exact drafts of the
  tiny shapes Visual C++ makes from a line or two of C, read straight from the code (getters, setters, constants, a
  global object's constructor call, thunks, vtable stores, float static initializers: `consoles/pc/drafts.py`), then
  Ghidra's draft made into C++ that compiles in the project (ghidra.h for its types, `this` for a method, callees
  declared by their calling convention). Each is judged like an agent's attempt. On a Visual C++ 6.0 game of 7,090
  functions, 670 have an exact draft and 524 matched for free (517 exact, 7 from Ghidra's drafts). The same drafts
  are made in the background (`app/drafts.py`, a source file at a time), follow the project's headers (a class that
  declares the function, a global's type), and `redraft` remakes one with an agent's declarations.
- **Windows PC: the frame pointer, per source file.** About half of a Visual C++ 2010 game's functions keep an ebp
  frame (/Oy-), which no C matched under the project's /O2. The import reads each source file's functions and builds
  the files that keep frames with /Oy- (`config/<version>/file_flags.json`, `scratch.frame_flags`); projects made
  before get it when the app starts, but for files with matched code. `search_flags` tries a file's flags (/Oy-, /O1,
  /Ob1) and keeps what matches, as `try_compiler` keeps a compiler.
- **Windows PC games bring their own DLLs.** Only the .exe was read, but many games keep much of their code in DLLs
  of their own (#12: a 2010 racing game's .exe is small; an engine and its game logic are two DLLs). Given by path,
  a game's folder or its .exe brings the DLLs beside it (two folders down, not in a launcher's or tool's own folder):
  each is a program of the project, its source files in `src/auto/<its name>/`, its functions named from its
  export table (functions.json's "names"), built with the Visual C++ its own Rich header names (the compiler per
  source file), its calls into other programs through its import table. DLLs are relocatable and many prefer
  0x10000000: the first keeps its address, a later one that would overlap is moved by its base relocations, as
  Windows moves it (delink.py does the same at build time), so func_/D_<address> stay names no two programs share.
  `ninja rebuild` and the rebuild check put each DLL back together against that DLL, and the import's other steps
  (thunks by its own import table, its debug symbols, classes, Reimport) read each program. Left out, each with why in the
  import's log: the C runtime, Windows' own names (a wrapper's ddraw.dll), middleware, copy protection, ASI plugins,
  ones the import can't start, and ones built a year or more after the program (mods). `consoles/pc/scratch.py`
  (find_modules), `Console.program_in` (a game that is a folder).
- **A Windows PC project rebuilds the whole game.** A PC project compiled each function and compared it with the one
  cut out of the user's .exe, but nothing put the program back together: its export was "—". `ninja rebuild`
  (tools/rebuild.py, standard library only) writes the whole .exe again, every function at its address from what
  `src/` compiles to where that is the game's code once its relocations are filled in from the project's symbols
  (a constant of its own, a string or a float, where the game has the same bytes), else from the delinked function,
  and the rest (headers, data, imports, resources) from the user's copy, and compares it byte for byte. It's the
  project's rebuild check too, so "Rebuild the game and compare" and every check of a join or a new analysis test
  the whole program with the matched C in it. On a Visual C++ 6.0 game of 11,846 functions it comes out identical
  with its six matched functions from C (constructors, destructors and exception handling), in under a second, and
  from a fresh clone of the project. Not a linker: the program's data isn't delinked, and Visual C++'s LINK.EXE would
  have to place every object, library and resource as the original did (and 2005's and later stop in wibo 1.2.0),
  so the code goes back where the game has it; moving code is the modding-ready check below.
- **A Windows game's own debug symbols.** A PC game that shipped its program database (.pdb), its linker's map or
  COFF symbols imported as if it had none. The import reads them now (Visual C++ 6.0's PDB 2.0 and the MSF 7.00 of
  .NET 2002 on, read by decompdecomp itself, no new dependency), using a PDB only with the build whose CodeView
  record names it: its functions and data get the game's names (functions.json's "names", methods as
  `Class::name`), its object files become the project's source files, and from a PDB its types, globals and free
  functions go into include/debug_types.h and each function's brief gets its prototype, parameters and locals. The
  demo built with 6.0's /Zi and /DEBUG imports with all 19 names and its two files, and C written with the game's
  own names and types matches. A .pdb or .map dropped with the game goes with it. No agent time; nothing leaves
  your computer.
- **The right Visual C++ pack at the drop.** A game of 2005-2010 was offered the 6.0-7.1 pack, and its import then
  asked for 2005-2010's: the page reads the program's Rich header (or its linker's version) as the import does.
- **Windows PC: reimport, and twins in and across your games.** Settings' Reimport works on PC projects: the
  program is analysed by Ghidra again and read by the newest rules (`scratch.reanalyse_program`), every function
  going to the source file that held its code, new code to files of its own, each matched function keeping its
  extent whatever the new analysis says there, names and each file's compiler kept, and the whole kept only when
  the code still rebuilds byte for byte. Twins never worked on PC: the targets are COFF objects, which twins read
  as nothing. They're fingerprinted now (`coffobj.py`), with names as C writes them, and code matched in one PC
  project is tried on its identical functions in another, where both files are built by the same Visual C++. A
  Visual C++ 6.0 game has 523 groups of identical functions (8 bytes or more) in itself; it and another PC game of
  the user's (a 2002 game whose program names no compiler) share no function of 32 bytes or more, so across games
  is proven on two hand-made projects only.
- **Windows PC: the compiler map, the judge's survey and range checks, over source files.** A PC game mixes
  Visual C++ versions (a 2010 game's Rich header counts files of 2010 SP1, 6.0, 7.0 and 2008), and each source file
  can have its own, but nothing measured which. `compiler_map` now works where a project's files each have a compiler
  (`app/filecompilers.py`): what the program's Rich header says built it (`Console.program_compilers`), matches by
  compiler, and the survey, which builds each file with agents' C with every compiler the program names
  (`ForkEngine.file_scores`, the file keeping its own): a function that matches with another and not its file's
  confirms it. The map spreads confirmations over neighbouring files (compilermap.build, a file for a function),
  and a run becomes a suggestion; a suggested compiler for a stretch of files, an agent's or the map's, applies only
  after a second file confirms it, walking out from the first and stopping at a file whose matches need its own
  (`rangecheck.evaluate`); refuted on both sides it's a quirk, with a note; a file nobody can tell goes to an agent.
  The Settings answer shows the survey and checks. On the demo, the file holding a function matched with 6.0
  refutes 7.1, built for real, and keeps 6.0; a Visual C++ 6.0 game's header names five 6.0 builds, so its survey
  has nothing else to try. DS has the code path (its files each have a CodeWarrior) but no Rich header and wasn't
  run.
- **Windows PC: function-end reports.** Ghidra sometimes runs a function on into the data after its return, or
  into the next function. `report_boundary` works on PC projects: once three agents of two models give the same
  end (an address, or an offset into the function), the function ends there and the rest becomes a function of its
  own, open to agents when it reads as code and set aside with what agents said when it doesn't
  (`scratch.cut_function`). As a join is, it's kept only when the game's code still rebuilds byte for byte, never
  touches a matched function, and one that doesn't hold is remembered. Cutting off only the padding Visual C++ puts
  in the function's own section is refused. ROM projects still ask the user. API helpers have the tool now too.
- **Windows PC: import thunks named and set aside.** A call to a DLL's function declared without
  `__declspec(dllimport)` goes to a `jmp [__imp__Name]` the linker adds. Ghidra marks those as thunks and the
  import left them out, so their bytes became one function with the code before them, and calls to them read as
  calls into its middle (`_D_0062F7A6`). Each is a function of its own now, named after its import (`_RtlUnwind@16`
  where Ghidra knows the import's arguments), set aside with why, and its import's name is its real name, so a
  caller's brief and draft read `RtlUnwind(...)` and C that declares it either way pairs with it
  (`scratch.name_import_thunks`). Projects made before get it at start, never touching a run a matched function
  calls into. On a Visual C++ 6.0 game: 22 thunks (WinSock, DirectX, Video for Windows, KERNEL32), 21 of them cut
  out of two functions of an older project, whose code still rebuilds byte for byte.
- **Windows PC games of 2005 to 2012 build with their own Visual C++.** A game built with Visual C++ 2005 or later
  imported but built with 7.1, so nothing of it could match. The `msvc2005` pack (proprietary, installed after the
  user accepts it) has 2005, 2005 SP1, 2008, 2008 SP1, 2010 and 2010 SP1, from the decomp community's archives, and each
  runs through the pinned wibo (2010's with the redistributable's `msvcp100.dll` beside it). The import builds a game
  with the build its Rich header names (`RICH_BUILDS`), or the closest of its version and says so, and tells a game
  whose compiler isn't installed which pack to install. The demo builds with each (`build.py 10.0SP1`), imports with
  that build, and agents' C matches. Functions inlined everywhere are found one by one between the int3s these
  compilers pad functions with: the demo built with 2010 had 14 of its 19 functions found, the five in the gaps run
  together. On real games: a 2005 game without a Rich header (its linker says 8.0) builds with 2005 SP1, its 28,777
  functions rebuild byte for byte, and 8 of 9 small functions written by hand matched on the first try; a 2010 SP1
  game builds with 2010 SP1, 27,023 functions, 6,583 exception-handling stubs set aside, and 4 of 8 matched at once
  (the rest: two return types guessed wrong, a function compiled with frame pointers, which `#pragma optimize("y",
  off)` gives, and a C runtime function built for size). That game keeps an ebp frame in about half its functions:
  if all of it was built with /Oy-, a project flag would serve better than a pragma per function.
- **PC projects get the newest build tools.** A PC project kept the `tools/` it was made with, so a newer names.py
  (constructors and destructors paired by their class) or delink.py never reached one. The template's tools have a
  version (`scratch.tools_version`, the manifest's "tools"): a project with another gets them when the app starts
  (`WindowsPC.on_start`, `scratch.write_tools`), keeping its compiler and flags, in one commit and feed line, kept
  only when the rebuild check stays as it was and tried once per version; agents' sources are never touched. A demo
  project made by the previous release got them with its rebuild identical.
- **Windows PC: `split_file`, and a compiler per source file.** A PC project's source files are runs of about 4 KiB
  of functions, so one can span two of the game's files, or two compilers (a game's Rich header often counts objects
  of Visual C++ 6.0 and 7.1). `split_file` divides one (`WindowsPC.split_source`: a unit of functions.json), kept
  only when the rebuild check stays identical and every match holds; each file can have its own Visual C++
  (`config/<version>/file_compilers.json`, which tools/configure.py reads), switched by `try_compiler` and the
  Settings panel as on the DS. On the demo, a file whose matched function 7.1 compiles otherwise stays with 6.0.
- **Newer PC games get source files of many functions.** From Visual C++ 2005 on, the linker aligns every function
  to 16 bytes with int3, and the import ended a source file at any gap between functions: a 2005 game got 19,028
  source files for its 20,863 functions, and a first build of 20 minutes. A gap that is only the padding the program's
  linker puts before most functions no longer ends a file (`consoles/pc/scratch.py`, `linker_padding`): the 2005 game
  gets 1,286 files, a 2010 one 1,324 (its Rich header counts 855 compiled files), and a Visual C++ 6.0 game, whose
  compiler pads its own functions, keeps its 1,851; the rebuild check stays identical. Projects made before keep
  their files.
- **A PC game's import says what it is and whether it can match.** A newer game said only "expect little to match",
  and a 64-bit one was refused at the drop with "decompdecomp starts PC games from their 32-bit x86 .exe". The import
  reads the program's headers (`consoles/pc/scratch.py`, `describe`): the Visual C++ version and build that compiled
  the game's own code (the Rich header's compilers, the build its linker shares; without a Rich header, the linker's
  version), how many files were built with whole-program optimisation (/GL), 32 or 64-bit, and the DRM or packer
  its sections show (SteamStub, SecuROM, SafeDisc, StarForce, Themida, VMProtect, Enigma, Denuvo, UPX), only named,
  never removed. 64-bit programs, Visual C++ 2017 and newer (out of scope for now), mostly-/GL games and wrapped ones
  aren't started, each with what it means; 2005 to 2015 games import, told they can't match until their compilers
  come.
- **C++ classes on Windows PC, by their own names.** A program built with `/GR` keeps each polymorphic class's
  name (`.?AVClassName@@`) next to its vtable, through the RTTI Complete Object Locator. decompdecomp reads it:
  `get_class` lists the classes (templates and nested names written as C++), each with its vtable, virtual functions,
  base classes and the functions that store its vtable (constructors, destructors); `get_function` names the class
  of a virtual function, and Ghidra's draft of a method reads `Class::func_<address>(...)` with `this`. Methods
  written that way match, and a renamed one still does. A 2008-era game: 1,522 classes, 3,517 methods drafted as
  `Class::method`; a 2014 rebuild of a 2004 game: 557 classes, 2,940; a game of about 2000 built without `/GR`:
  only the 34 classes of its MFC library. Their fields, and the classes of code built without `/GR`: next item.
- **Struct layouts and class fields on Windows PC.** x86 has its reader of what each function does with pointers
  (`arch/x86_fields.py`): `this` in ecx, the arguments on the stack (through esp, followed across pushes, calls and
  `__chkstk`, or an ebp frame), the words loaded from them, globals. `guess_struct` and the brief's layout lines
  work on PC projects as on MIPS games (a struct through `this`, arg0-arg3, what a function returns, a global), and
  `get_class` shows each class's fields (offset, type, which functions use them), merged across its methods, with its
  bases' inherited. Classes of code built without RTTI come from the vtables its constructors store, named
  `Class_<vtable address>` until the vtable gets a real name. The demo's entity struct comes out field for field
  (bar one written only as an array element); a C runtime's FILE comes out as FILE is. A game of about 2000 built
  without `/GR`: 458 classes (424 from its code), 4,917 of its 11,696 functions with a layout for an argument; a 2001
  game without RTTI: 38 classes (every vtable its code stores), 2,544 of 4,793; a 2008-era game: 1,980 classes (458
  of code built without RTTI), 15,632 of 20,863; a 2014 rebuild of a 2004 game: 593 classes, 11,664 of 24,784, its
  entity classes' position, matrix and model index where the community's headers have them. Reading a game takes 5
  to 15 seconds, once per change to the program or its functions' extents (a rename reads only the names again).
- **Constructors and destructors on Windows PC.** A Visual C++ game's small exception-using functions are often
  constructors and destructors, and a class's two are at two addresses: named after an address, only one paired, and
  a destructor written so (`func_004D9DD0::~func_004D9DD0`) was refused outright. An agent now writes
  `CFoo::CFoo` for one function and `CFoo::~CFoo` for another; the attempt gives each that real name, and names.py
  pairs `??0CFoo@@...` and `??1CFoo@@...` with the original's symbols, calls from other functions included.
- **Windows PC: exception-handling stubs are out of agents' way.** C++ built with Visual C++'s `/GX` puts the code
  that destroys a function's local objects during an exception, and its handler, in `.text$x`, which the linker puts
  after all of the program's code. Ghidra takes each stub for a function, the smallest in the project, so agents were
  handed them first. The import now sets aside the stubs in the run that ends the code (`scratch.exception_stubs`),
  with why (functions.json's `generated`), and projects made before get it at start. On a 2000s Visual C++ 6.0
  game, 4,677 of 11,877 functions, every one in that run and none in the main body or the C runtime; on a 2000s game
  rebuilt with Visual C++ 2008 tools, where dynamic initializers follow the run, 6,581 of 24,843. Hand-written
  assembly on PC isn't recognised yet (x86 has no `asm_reason`).
- **Pieces of functions joined on Windows PC.** Ghidra starts some functions in the middle of others: a lone
  epilogue after a call it took for one that never returns (`throw`), a catch block or an `__except` filter after
  the function's return. Neither part could match. Code that reads or restores ebx, esi, edi or ebp before saving
  or setting it is joined to the function before it when that one runs on into it, set up the ebp frame it reads,
  or jumps into it with a short jump (`arch/x86.piece`, `consoles/pc/scratch.merge_pieces`): on a PC game of about
  2000, 181 of 11,877 functions. MSVC's exception-handling funclets at the end of the code read their parent's
  frame too, and the first follows a function with a frame of its own: they're set aside first, and no join takes one.
  PC projects have a rebuild check now: the program's code put back together from the delinked functions
  (`scratch.check_code`). It found two older analysis faults, fixed for new imports: functions found in gaps lost
  the zero bytes their last instruction ends with (`ret 8`), and a jump table running into the next one had its
  entries relocated twice. Older projects get what the import learnt when the app starts or on Check again, one
  step at a time (the manifest's `analysis`): the repairs, the stubs set aside, then the pieces joined, never a
  stub; each step that changes code is kept only when the code still rebuilds byte for byte (on that game: 23
  tables, 390 functions, 179 pieces). Agents' `join_functions` works on PC too.
- **Saved guides keep your notes.** Saving creates a basic README only when one is missing. Later saves update
  counts and visuals inside hidden comment pairs, preserving the rest of the guide. A local SVG progress card
  shows completion, platform and a function map in ROM and project mode; it contains no game code.
- **Real names on Windows PC.** A PC project names functions and data by address (`func_0041B470`, `D_0066DDA4`),
  and `tools/names.py` pairs them with the original's symbols by that address, so rename_symbol refused them. A real
  name is kept by address in `config/<version>/functions.json` ("names"), and names.py takes it as that address in C
  and C++ (a method's too): the function and its callers still match. The report keeps the original's symbol, so the
  function's row does too, and agents find and call it by its real name.
- **The compiler of each object file, from a PS2 game's symbols.** Games linked by CodeWarrior's linker are often
  GCC's code with CodeWarrior's C library: the three with symbols had GCC's mark on 91-93% of their object files.
  The one that built most of the code is the game's, and SN's GCC is told from Sony's by its own code; the others'
  object files get their ranges at import, and Check again suggests them to older projects through the range check.
- **Polishing reaches every function, then tidies each file.** A floor of 8 instructions and a ceiling of 400 kept
  polishing off a game's smallest and biggest functions for good: every size comes now, the middle first, and a
  function three tries didn't make readable is left. Once a source file's functions are all readable, a helper tidies
  it: types it shares with other files in one header, fields and classes named, the globals it still knows by
  address named (read_data first), dead declarations gone, one naming style (`tidy_done`).
- **Every compiler of the console, then the test of them all** (N64, PS1, PS2). The import offers to install the
  console's missing compilers, and a project page offers it until the first match; the compiler test then tries
  every one. A guess from the program alone can miss: one game's GCC code leaves its return delay slots empty as
  often as CodeWarrior's.
- **PS2 programs splat couldn't read** (#44): one without section headers, one whose DNAS block is marked as
  register info, one whose sections have no names, one with an empty section below its code. All import now.

- **A CodeWarrior version per source file on GameCube and Wii** (#26). A project started from scratch builds a
  source file with its own version when its `Object(...)` names one (`mw_version="GC/1.2.5"` in configure.py, which
  dtk-template reads): `try_compiler` switches a function's file, or tries them `"all"`, and Settings switches a
  file, a range or the whole game, keeping every file whose matches still hold. An agent found a file whose code
  matched only with GC/1.2.5 and GC/1.1 where the project built everything with GC/1.3.2; a file that mixes versions
  is split first (`split_file`), then the new file switches. Public decomp forks still build with their project's
  own compilers.
- **Visible progress while waiting.** Saved projects, room setup, exports, project checks, compiler changes and
  installations show a labelled work bar beside their logs. The bar clears on completion or failure; saved-project
  cancellation clears it too. Each action says when it starts and finishes, or why it stopped. Upload percentages
  still measure bytes transferred.
- **Names in project mode.** rename_symbol renames a function or a global in a project too: in the game's symbols
  (DS: ds-decomp's symbols.txt; GameCube, Wii: dtk's) and in every source and header file, kept only when the game
  still rebuilds byte for byte and every match holds. Polishing's names were local only before (func_0205d0f4,
  data_0222f194 stayed). Windows PC's names come from addresses (`names.py`): it says it can't rename yet.
- **Polishing on every console.** Helpers that make matched code readable (real names, types, a comment) worked on
  ROM projects only: a DS game at 57% had nothing to choose. In project mode a rewrite goes through the project's
  own file and is judged like any compile of a matched function: committed when it and the file's other matches
  still match, else the file is put back. Helpers there keep the function's name and describe types in the project's
  headers (edit_file).
- **Agents join what the rules miss** (project mode). `join_functions(function, with_function, reason)`: once 3
  agents of 2 models say two functions are one the analysis cut, the console joins them (DS: `recover.join_functions`,
  across two source files too), checked as an analysis is (the game rebuilt byte for byte, else taken back), never a
  matched function; a refused join is remembered. Check again only ever joins, so what agents joined stays. Other
  project consoles keep the agents' agreement until they can join.
- **What no C makes is out of agents' way, and progress counts the rest.** On a DS game 159 of 557 open functions
  were pieces of others (ds-decomp's symbols in the middle of functions: a lone epilogue, a register save running on
  into the next symbol, a lone `bx ip`), each given up on by five to seven agents in turn. ARM code that pops pc
  without having pushed, jumps through a register its caller set, uses r4-r11 before saving them or never returns
  is set aside (`arm.piece`), and a
  DS project joins such pieces to the function they belong to (`recover.merge_pieces`, checked by dsd): on that game
  119 joined, 4 functions matched at once with the C agents had written, no match lost, the ROM still identical.
  What shapes can't tell, agents can: `release_function(..., not_c=true)` with the reason, and once 3 agents of 2
  models agree the function is set aside, red in the map, until the user gives it back. Percentages and counts leave
  set-aside code out. **Check again** (About this project) looks at a project with what the running version knows;
  it also runs before helpers start, once after each update.
- **PlayStation (PS1) games from your own copy** (beta): a disc image (BIN, ISO or CHD; SYSTEM.CNF says which
  program it boots) or its PS-X EXE, with the disc's overlays. Functions are built as PSY-Q games were: GCC 2.6.3,
  2.7.2 or 2.8.1, then maspsx, which does what Sony's assembler ASPSX did, then GNU as; the import tries the three on
  the game's code. BIOS call stubs and GTE code are set aside as assembly. The demo game matches function by function
  and rebuilds byte for byte; no real game is tested from start to end yet. A disc whose boot program is a loader
  gives the program it loads (`exe.loaded_program`), and where splat's guess at the code misses the entry point (a
  loader that is mostly a picture: 1 function found), .text is where the code's returns and frames are (137).
  Overlays inside an archive file (a game's areas, each with its code, one after another) are found too
  (`overlays.embedded`): on one PS1 game, 77 overlays in a 68 MB file, 26,483 functions besides the program's 2,398.
- **Nintendo DS games from your own copy again** (beta, #23): a ds-decomp project for the ROM, as in 1.0.2, now with
  all 24 CodeWarrior for DS versions and a version per source file (`config/<version>/arm9/file_compilers.json`, as
  DS decomps keep it). `try_compiler` switches a function's file to another version, or tries them `"all"`, and
  keeps the one it matches with when the file's other matches still hold: a game whose code mixes 1.2 and 2.0
  (1.2's `ldmia {..., lr}; bx lr` epilogue) no longer sticks at 80-95%. Machine drafts are the gap: a ds-decomp
  project keeps no assembly for m2c to read.
- **DS functions only pointers reach.** dsd's analysis follows calls, so callbacks, state tables and everything they
  call stayed outside any function (77% of one game's main program), and functions calling across those gaps never
  reached 100%. `consoles/nds/recover.py` finds them by their starts, adds their calls and pointers, and drops what
  dsd refuses to delink: 1,784 more functions in that game, every match kept. Projects made before it get them once
  when the app starts (`ForkEngine.reanalyse`): kept only when the rebuilt ROM is unchanged, and the project's
  activity says so. dsd also ended `.text` at the last function it found, and the 45 KB of code after it was
  `.rodata` to it, its 19 calls from `.text` unrelocated: `.text` now ends where that code does
  (`recover.extend_text`), with 207 more functions and 13 source files of their own. 2,987 functions in all.
- **A DS game rebuilt byte for byte.** The link stripped what nothing points at, and nothing points at an overlay
  holding only data (the game loads it by number): those came out empty, every overlay after them moved, and the
  rebuild differed in 209 overlays and every pointer to them. It links everything now (`-nodead`), and the header
  gets the copy's secure area checksum (of the area encrypted, which a dump no longer is; dsd left it 0). The game
  that differed rebuilds identical, its 1,784 recovered functions included. Projects made before get their build
  fixed at start (`scratch.write_tools`, kept unless an identical rebuild stops being one).
- **The permuter's real rewrites in forks.** decomp-permuter got no header in a fork, so it refused every
  function ("Syntax error in base.c": `u16` was no type it knew) and only the six simple rewrites ran, in every
  fork and on DS. It now reads what the function's file includes and declares.
- **Model routing.** Each model's share of its claims that matched, in the project, tells the models at work
  apart (on an N64 game free models matched 14-25% of what they claimed, stronger ones 42-77%). A model well behind
  the strongest one at work leaves functions given up on three times to it, and the strongest takes those first;
  near misses already go to the permuter. Nothing changes until two models have 20 claims each.
- **Helpers' effort.** The launcher's Effort, opencode's until now, is every helper's: Claude Code's (`--effort`,
  Low to Max with Extra high), Codex's (`model_reasoning_effort`, Minimal to Extra high) and the API helper's
  (OpenAI's `reasoning_effort`, Minimal to High, for servers whose models reason). A model at another effort is a
  helper of its own (`claude-opus-xhigh-1`), so its matches and spend are counted apart.

- **Agents try before they release, and say what a function waits for.** On a large x86 game agents released
  functions seconds after claiming them, untried (12,565 releases, median 5 s). release_function now answers a
  release before compile_and_diff's move-on rule once with what to do; a release without a compile doesn't count
  against the session; `needs=<function>` records a prerequisite: the function waits (claimed last), the one it
  needs goes first and is handed over, and the match brings the waiting one back first. Notes saying "already
  matched" on a function that doesn't match are marked out of date.
- **Rooms that say what's happening** (1.0.4-alpha). A guest waiting for the host sees "Waiting for the host to let
  you in" over the project, not an empty one and a red "Room offline"; then "Getting the host's work", counting down.
  A new guest starts at the room's newest event instead of replaying its history into the feed and notifications,
  and one that was away catches up a page at a time (the sync started from nothing at every start). Each computer
  has a colour on its agents' names, the same on every screen, and the Agents tab lists what the room's other
  computers' agents are on. A git project's join waits to be let in (with approval on, it failed), and a declined
  or removed guest is told so.
- **Room presence during slow syncs.** A separate heartbeat keeps members online while importing the host's work
  or rebuilding a source project. Sync failures and recovery are logged, and syncing continues automatically
  after a temporary connection failure. Applies to ROM and project rooms on every console.
- **A standalone app.** Its own window (GTK and WebKitGTK) and a tray icon, so closing the window doesn't stop
  helper agents; **Quit** ends everything the app started. Only helpers the app starts work on projects: the old
  `decomp mcp` bridge is gone, and the first start removes its registrations from your agents. A development copy is
  a separate app (`~/Decomp-dev`) that never touches the installed one.
- **Helpers that restart by themselves**, with backoff; on a usage limit they wait until it resets. The API helper
  (any model with an API key) is the main helper: the app runs its whole loop.
- **One notification centre** (the bell) for every project, and **Report a problem** on every page.
- **Save and restore** (ROM projects): the work as plain files in `progress/`, every few minutes; **Open a saved
  project** rebuilds it on another computer from your own copy, and judges every function again.
- **Reimport a ROM project** (Settings): replace the split from a newly selected copy, keeping matched C only where
  its target is unchanged, plus notes, settings, saves and the names agents gave (functions are paired by address).
  A matched function the new split doesn't have keeps its C in a note. PC projects reimport too (Ghidra again);
  other project-mode consoles can't yet: their project's own analysis makes the functions, and Check again redoes
  what decompdecomp adds to it.
- **Polish matched code** (ROM projects): helpers give matched functions real names, types and a comment; a change
  counts only when the function still matches. The project shows matched and readable.
- **Shared knowledge**: what agents learn about a game reaches every agent once helpers of two different models
  agree on it.
- **Twins across your games**: code matched in one of your games is tried on the identical functions of the others
  (libraries, the compiler's runtime). No agent time; nothing leaves your computer.
- **A real permuter**: decomp-permuter rewrites near misses on the parse tree, weighted per compiler, on spare
  cores at a low priority. On nine near misses made from the demo ROM's functions it matched all nine, the old
  regex rewrites five. They stay as the fallback, for C decomp-permuter can't parse.
- **Better drafts**: m2c drafts every function in the background, from the header declarations its code uses, and
  again when they change; `redraft` runs m2c with an agent's own declarations; methods of known C++ classes are
  drafted as `Class::method`. On GTA San Andreas, 37 of 40 sampled drafts read the header (none did before).
- **Flag search** (`search_flags`, MIPS): each flag of the compiler changed once, up to 12 builds.
- **PS2 debug symbols** (#3): names and sizes from the symbol table; from STABS the source files as units,
  prototypes, parameters and locals in the brief, every type and global in the header; the same from the DWARF 1
  CodeWarrior writes (.debug, .line), with the line each function starts on. Big headers: the brief
  carries only the declarations the function uses.
- **PS2 kernel calls named**: the stubs (`addiu v1, N; syscall; jr ra`) get the kernel call's name: 154 in GTA San
  Andreas, 129 in Ridge Racer V.
- **Consoles as packages**: one folder per console (`consoles/<id>/`), a template and a conformance test. PS1 and
  GBA are public decomps only now; the git history keeps their code.
- **macOS app**: a native Cocoa/WKWebView window with the private window user agent,
  native open/save panels, downloads, Edit/Reload/Quit menus and the page's close confirmation. A uv source
  installer creates a Finder app in `~/Applications`. Intel and Apple Silicon CI exercise portable regressions
  and the hidden native window. KMC GCC/binutils require Rosetta on Apple Silicon; unavailable Linux-only
  compilers remain unavailable. PS1 GCC 2.6.3 builds natively from pinned source on Intel and Apple Silicon Macs
  with Xcode Command Line Tools; GCC 2.7.2 and 2.8.1 use pinned native Apple Silicon builds with maspsx. The
  assembler helper uses the app's Python rather than Apple's older system Python. Mac workers use per-thread QoS
  so they don't lower the whole app's priority.
- **Linux packages**: a `.deb`, an `.rpm` and an AppImage with their own Python, built for each release.
- **Windows native app**: a WebView2 window, Start menu and desktop shortcuts, and a native tray icon.
  A custom native caption follows the app's theme, with Windows resizing, snapping and drag-to-restore. Scrollbars
  follow the page's theme too. Closing asks whether to keep helpers working. Kernel jobs end helper children after
  a session or an app crash. Tools have pinned Windows builds; Linux-only compilers use WSL 2 with Linux-side tool
  and build caches. Windows CI checks the native and portable code beside the existing Linux checks.
- **Struct layouts from loads and stores** (MIPS: PlayStation 2, N64). Every load and store through a function's
  pointer arguments, the pointers loaded from them and globals, made into types along the call graph: the brief
  names each pointer argument's likely fields (offset, width, how many functions agree), `guess_struct` writes the
  struct as C, says where else the type flows and which name matched code already gives it. Checked against the
  structs agents gave matched functions on GTA San Andreas: 95% of the offsets it proposes from other functions are
  right. PowerPC and x86 need their instruction readers (`Arch.field_accesses`).
- **Range checks** (MIPS ROM projects). An agent's "this range was built by another compiler" is a hypothesis: the
  judge tests the functions next to it (their matches, agents' attempts, the survey, habits), and where nothing
  settles it the nearest neighbour goes to an agent as a task. A range applies once a second function matches only
  with its compiler; refuted neighbours on both sides make it a quirk (dismissed, with a note on the function). The
  applied ranges are checked again the same way, by themselves. On GTA San Andreas's 72 ranges: 12 confirmed, about
  25 resting on one function, about 36 with no function whose C needs their compiler.

- **Compiler map** (MIPS: PlayStation 2, N64): which compiler built each part of the game. Measured on
  GTA San Andreas, compilers' habits only rule compilers out (-G0 code never reads through `$gp`; its CodeWarrior
  code never uses branch-likely), so what rules a compiler in is the judge: the survey builds agents' C for unmatched
  functions with each compiler the project uses, and C that matches with another compiler (and not today's)
  confirms it. Confirmations spread over their neighbours (one object file is one compiler) up to the furthest
  evidence; a run big enough becomes a suggestion, checked against every matched function. The ROM map shows it
  (Compiler view); agents read it with `compiler_map` and in `get_function`.
- **C++ classes from the game's code** (MIPS: PlayStation 2, N64). Constructors store their class's
  vtable into the object; from those stores decompdecomp finds each class, its virtual functions, its base class and
  every field its functions touch (offset, type). Agents read them with `get_class`; `get_function` names the fields
  of `this` a function uses. On GTA San Andreas: 507 classes, and the recovered entity layout (position at +4, matrix
  pointer at +0x14, model index at +0x22) is the one found by hand. Why: functions with virtual calls matched 7% of
  the time against 62% for the rest, because agents guessed the layouts.

## Next

### Newer Windows games (Visual C++ 2012 to 2015)

PC projects build with Visual C++ 6.0 to 2010 (phases B and C are done: Recently done). The import already tells a
newer game apart (its build, /GL, 64-bit, DRM); what's left, one phase at a time. Games built with Visual C++ 2017
and newer are out of scope for now.

- **What 2005-2010 still lacks.** The newer exception-handling tables (`__CxxFrameHandler3`) need the stub rules
  tested again (a game rebuilt with 2010 tools: 6,583 stubs found; a 2005 game built without C++ exceptions: none).
  2005's RTM isn't in the pack: its Rich header build is SP1's (50727), so SP1 builds both, and a game of 2005-2006
  may need RTM (decomp.me's `msvc8.0`). wibo 1.2.0 doesn't run these compilers' linkers (`lstrcmpiW`, added to wibo
  after it), which only the demo's build needs. On a Windows computer the pack is untested: there the compilers need
  their C runtime (msvcr80, msvcr90 through side-by-side manifests, msvcr100), which wibo has built in.
- **D, 2012-2013, then 2015.** The same, per generation: SSE2 float code by default from 2012, and one toolset for
  2015's updates.
- **E, whole-program optimisation.** With /GL the linker generates the code for the whole program, so one file's
  object says nothing about the program's bytes. Find out whether some functions can be judged by compiling their
  file with /GL and linking it with /LTCG against stubs; until then a mostly-/GL game isn't started.
- **F, 64-bit.** Read PE32+ programs and delink their relocations (nearly all relative), with the x64 calling
  convention. A 64-bit program lists every function's start and end in its exception directory (`.pdata`), so
  functions come from the program, not Ghidra's guess. Done when a 64-bit demo, then a real game, imports and
  matches.

### PSP: encrypted programs, ZSO images, imported names
Most retail PSP programs (EBOOT.BIN) are encrypted, and decrypting them takes Sony's keys: decompdecomp doesn't
carry them, so the user imports a decrypted copy (PPSSPP can save one). A pinned, checksummed decrypter installed
from Setup, like a compiler, would let a UMD image import as it is, and its encrypted modules with it (left out
now). Also: a module's own $gp isn't the judge's (one per project: a module that keeps small data where the main
program doesn't gets its own flags, but its $gp-relative data is named from the main program's $gp), its imported
variables (.rodata.sceVstub) aren't named, ZSO images (LZ4) aren't read, SN's pspsnc is tested on the demo only (no SN-built game was at hand: the
readable one is CodeWarrior's, the others encrypted), and the 2.4.1 CodeWarrior some games were built with isn't
archived (3.0.1 matches small functions;
bigger ones unverified).

### PSP drafts: the Allegrex's own instructions
m2c has no Allegrex target: the EE's reads PSP code, and knows `seb`, `seh` and `madd` but not `min`, `max`, `ext` or
`ins`, which PSP GCC emits from plain C. Drafts have `M2C_ERROR(unknown instruction)` there, and those functions
rarely match from autopilot (the guide tells agents what each one is). Rewriting them for m2c before it reads the
code (`min` as `slt` and `movn`, outside delay slots) would give those drafts their arithmetic back.

### Windows distribution

The Windows release MSI bundles Python and the app, creates Start and desktop shortcuts, and updates
through the app. Next: sign the installer and check for WebView2 and WSL during setup.

### Better splits reach existing ROM projects

A ROM project (N64, PS2) is split once, when it's made: a better split (code splat misses, function ends, compiler
ranges) only reaches games added after it. DS projects get theirs at start (`NDS.ANALYSIS`,
`ForkEngine.reanalyse`): versioned, kept only when the rebuild is unchanged, and the project's activity says so.
ROM mode needs the same, with more to carry: a split version in each project, and on start a new split whose
functions take over the old ones' work (matches, attempts, notes, claims) by address, kept only when every matched
function still matches and the export still rebuilds the game byte for byte. Functions the new split cuts or joins
go back to the queue with their old attempts as notes. Each improvement to the split bumps the version.

### How much usage each agent has left

A window that shows, for each agent you use, what's left of its limits: the 5-hour and weekly limits of Claude Code
and Codex, and a monthly limit or the credit left on an API key where the provider says it. Helpers already wait for a
usage limit to reset; this shows it before they hit it, so you can pick which agents to start.

### Big functions for every model

In a DS game at 58%, functions under 400 instructions were mostly matched, and 25 of the 29 over 400 were open:
40% of the code left, 10 of them never compiled. Agents of free models released them as "too big to transcribe in
one go", and they're right about the medium: a big function's Ghidra draft is 10-25 KB of C, more than one
compile_and_diff a model with a small output limit can send, and its assembly takes most of a small context.

The big ones that matched show the way. A 2,793-instruction switch matched in 2 compiles as 125 lines of C, and a
1,272-instruction one by a free model, in 2 compiles too: their cases are long runs of the same calls with other
numbers, and the agent wrote one-letter macros for them (`#define A(x) func_0206d4f8(x, 1)`). Two of the untried big
functions are in that file, macros defined, and their briefs don't show the match: a matched look-alike's C is shown
only under 3,000 characters.

1. **Briefs for big functions** (no new tool). Over 400 instructions, the brief says how such functions matched
   (macros for the runs of calls that repeat, the file's own macros listed), and shows a matched look-alike's C
   whatever its length, with its macros.
2. **One case at a time.** A `compile_part(function, part, code)` tool: the function's current C stays in its file,
   one part (a switch case, from the target's jump table: ARM's `add pc, pc, r0, lsl #2` and the branches after it;
   or a block between labels) is replaced, the unit is built and judged as always, and the answer is that part's
   rows of the diff. A first compile sends the skeleton (prologue, the switch, empty cases, epilogue). Each call
   carries 100-200 instructions' worth, which any model handles. The judge doesn't change: the function matches
   when objdiff says 100% for all of it.
3. **Several agents on one function, at once.** With parts, a big function can be held in parts: each agent claims
   a part, works on it with compile_part, and its change stays when the function's score gets no worse and no
   other part's rows get worse (the compiler allocates registers over the whole function, so a case can move
   another's; such a change is refused, saying which part moved). Agents see each other's parts land, through the
   file, and notes per part. Briefs give each agent its part's assembly only.

Measure each step on that DS game's 25: how many get tried and how far, against the week before. Step 2 is what
matters most for free models: step 3 adds speed, and contention on one file, so it waits until 2 shows parts can be
worked on alone.

### Library decomps built locally

Twins across your games already match library code you matched once. The next step: match library decomps and
libgcc/newlib, built on your computer with the project's compiler (libultra on N64, the PS2 SDK libraries), and try
them as twins. Library sources are fetched only when you ask, never bundled: SDK code is the console maker's
copyright.

### Names from another build you own

Many games have a second build with symbols: a demo, a prototype, or a port to another platform. C++ symbols say the
class and the parameter types, so carrying them over gives agents real signatures. Pair functions by the text and
constants they use, spread the pairs through the call graph, and let the user approve the names in bulk (as boundary
reports are approved now). Names only: matching is unaffected. It also names the classes `get_class` finds.

### C++ classes for PowerPC

The class recovery reads MIPS and x86 (Windows PC: RTTI's names, the classes of code built without it, and their
fields: done). Left: GameCube and Wii (CodeWarrior: the vtable pointer often isn't at offset 0), with a PowerPC
reader (`Arch.field_accesses`), in fork mode.

### A modding-ready check for the export

The export rebuilds the game byte for byte; mods need code that can move. Build a second copy with padding inserted
and report every address that is still hard-coded ("93% movable, 41 addresses left"). The matching build stays as
it is. A Windows PC project's rebuild puts each function back at the game's address in the user's copy
(tools/rebuild.py): moving code there needs its data delinked too, and a linker that lays the program out.

### A list of open rooms (to investigate)

Asked for by a user: a page where you see the rooms running now and join one. Hosts would still host their own
rooms, as now (the host's decompdecomp is the room, through its tunnel or address); a small directory server would
only list the public rooms whose hosts chose to list them: the game's name and hash, the platform, % matched, members,
and the room's address, dropped when the host stops answering. Joining is unchanged: the guest's own copy of the game
must have the host's SHA-1, and only the work travels.

Before building it, find out:

- **Is it allowed?** What a public directory of rooms means legally for the project, in the places it's used: what
  it may show (titles are trademarks: name only, no box art or screenshots), whether listing a room makes the
  directory responsible for what the room does, and what notice-and-takedown it needs. Check that nothing a listed
  room sends is the game's code or data, for git projects too (forkroom.py: what a joining guest gets of the host's
  tree).
- **Who runs it, and for what cost.** The site is static (GitHub Pages), so the list needs a host of its own; a
  small worker with a heartbeat per room may be enough.
- **Abuse and privacy.** Listing is opt-in per room and off by default; a room on a plain address shows the host's
  IP, so only tunnelled rooms could be listed; room names need limits, and a host or the directory can delist one.

The answer goes here: build it, build part of it, or not, and why.

## Later

- **Better look-alikes.** A second measure (edit distance over opcodes) next to today's, and shared runs of
  instructions, which find inlined helpers and macros. Needs PowerPC and x86 opcode readers.
- **Decoders for data formats.** Tools that decode N64 display lists, PS2 GS/VIF packets and GameCube GX command
  lists for agents.
- **The permuter's C, tidied.** A matching variant from decomp-permuter can be hard to read; a polishing helper
  could tidy it, still matching.
- **Polish in the drafts.** Methods of known classes are drafted as `Class::method` in the brief; the polishing pass
  doesn't use them yet.
- A Flatpak on the GNOME runtime.
- macOS signed/notarized distribution and a menu bar tray icon.
- macOS memory, CPU and file-size caps for compiler processes (Linux currently uses systemd scopes).
