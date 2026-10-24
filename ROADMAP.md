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

| Feature | N64 | PS1 | PS2 | GameCube · Wii | Windows PC | DS | Public decomps |
| :-- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Status | recommended | beta | beta | beta | beta | beta | the project's own |
| Start from your own copy | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | fork by address |
| Judge | link · compare | link · compare | link · compare | objdiff | objdiff | objdiff | objdiff |
| Machine drafts (m2c, Ghidra for x86) | ✓ | ✓ | ✓ | ✓ | ✓ Ghidra | — | by architecture |
| Autopilot: free matches from drafts | ✓ | ✓ | ✓ | ✓ | — | — | — |
| Drafts follow the header, made in the background; `redraft` | ✓ | ✓ | ✓ | — | — | — | — |
| C++ methods drafted as `Class::method` | ✓ | ✓ | ✓ | — | ✓ (RTTI) | — | — |
| Twins: free matches from identical code | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Permuter on near misses | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Hand-written assembly set aside | ✓ | ✓ | ✓ | ✓ | Visual C++'s exception-handling stubs | BIOS call stubs, pieces of functions | MIPS, PowerPC, ARM's BIOS stubs and pieces of functions |
| Pieces of functions joined to their function | — | — | — | — | ✓ (`scratch.merge_pieces`) | ✓ (`recover.merge_pieces`) | — |
| Agents' verdict "no C makes this" (3 agents of 2 models: set aside, red in the map) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Check again (and before helpers start, once after each update) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Kernel stubs set aside and named | — | set aside (BIOS calls) | ✓ | — | — | — | — |
| Function-end reports (`report_boundary`) | ✓ | ✓ | ✓ | — | — | — | — |
| Compiler ranges and suggestions | ✓ | ✓ | ✓ | from scratch: per source file (`try_compiler`, Settings); else the project's build | the project's build | per source file (`try_compiler`, Settings) | the project's build |
| Flag search (`search_flags`) | ✓ | ✓ | ✓ | the project's build | the project's build | the project's build | the project's build |
| Compiler map and the judge's survey | ✓ | ✓ | ✓ | — | — | — | — |
| Range checks: a range applies after its neighbours confirm it | ✓ | ✓ | ✓ | — | — | — | — |
| C++ classes from the code (`get_class`) | ✓ | ✓ | ✓ | — | named by RTTI; no fields yet | — | — |
| Struct layouts from loads and stores (`guess_struct`) | ✓ | ✓ | ✓ | — | — | — | — |
| The game's own debug symbols: names, types, prototypes | — | — | ✓ symbol table, STABS; the compiler of each object file | — | — | — | — |
| Twins across your games | ✓ | ✓ | ✓ | — | — | — | — |
| Shared knowledge (two models agree) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Polish matched code (still matching) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Real names for the game's symbols (`rename_symbol`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | dtk and ds-decomp projects |
| Save and restore (`progress/`, Open a saved project) | ✓ | ✓ | ✓ | the project's git; best attempts saved | the project's git; best attempts saved | the project's git; best attempts saved | the project's git; best attempts saved |
| Saved README preserves notes, with completion bar and function map | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reimport: a new split, matched work kept (Settings) | ✓ | ✓ | ✓ | — | — | — | — |
| Rebuildable export | ✓ | ✓ | ✓ | the project | — | the project | the project |
| Progress bars for foreground jobs | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Rooms: decompile together | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Where to start on a gap:
- **PowerPC (GameCube, Wii) and x86 (PC)**: class recovery and the compiler map work on a project's objects in fork
  mode, not on a ROM; the readers are the part to write (CodeWarrior's vtable pointer is often not at offset 0). A PC
  program built with RTTI has its classes named already (`consoles/pc/rtti.py`, a `Console.classes` part); its
  fields need x86's `Arch.field_accesses`.
- **Windows PC**: autopilot needs drafts that compile (Ghidra's output doesn't, as it is); the export needs a linker
  story (the project is delinked COFF objects). Only games built with Visual C++ 6.0 to 2010 can match so far: Newer
  Windows games, below, is the plan for 2012 to 2015 and 64-bit. A class's constructors are one real name
  (`CFoo::CFoo`), so of two at two addresses (a copy constructor) only one can be written as a constructor: names.py
  would need the mangled signature in "names". A project keeps the `tools/` it was made with, so one made before a
  change to names.py (as constructors' names) needs those files from `consoles/pc/project_template/tools/`: an upgrade
  step is missing (a step of `WindowsPC.on_start`'s, as the DS has). `split_file` and a compiler per source file
  (`try_compiler`) need the console's `split_source` and `set_file_compiler`: a unit of functions.json and its file,
  and a compiler per file where configure.py has one `MSVC_VERSION`; the rebuild check (`scratch.check_code`) can
  check a split now.
- **Function-end reports** cut MIPS functions (`engine._cut`).
- **Struct layouts** read MIPS loads and stores (`arch/mips_fields.py`); another processor adds its reader as its
  `Arch.field_accesses`, and structs.py does the rest. Still open on MIPS: the PS2's fifth to eighth arguments
  (t0-t3), and a pointer into the middle of a struct handed to a callee (`&this->pos`).
- **Twins across games** and **restoring a save** work on ROM projects (`app/twins.py`, `app/saves.py`). In project
  mode a project's matched C is its git repository; what's missing there is matching identical code across your
  games (their objects, not a ROM, hold it) and opening a save's best attempts in a new copy of the project.

## Recently done

- **Windows PC games of 2005 to 2012 build with their own Visual C++.** A game built with Visual C++ 2005 or later
  imported but built with 7.1, so nothing of it could match. The `msvc2005` pack (proprietary, installed after the
  user accepts it) has 2005 SP1, 2008, 2008 SP1, 2010 and 2010 SP1, from the decomp community's archives, and each
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
  only the 34 classes of its MFC library. Fields aren't read from x86 code yet.
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
  A matched function the new split doesn't have keeps its C in a note. Project mode can't yet: its project's own
  analysis makes the functions, and Check again redoes what decompdecomp adds to it.
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
  prototypes, parameters and locals in the brief, every type and global in the header. Big headers: the brief
  carries only the declarations the function uses.
- **PS2 kernel calls named**: the stubs (`addiu v1, N; syscall; jr ra`) get the kernel call's name: 154 in GTA San
  Andreas, 129 in Ridge Racer V.
- **Consoles as packages**: one folder per console (`consoles/<id>/`), a template and a conformance test. PS1 and
  GBA are public decomps only now; the git history keeps their code.
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
  Their linker pads functions to 16 bytes with int3, and `scratch.units` ends a source file at any gap: a 2005 game
  got 19,028 source files for 20,863 functions, a 2010 one 18,328 for 27,023, and a first build of half an hour; a
  file should run on across that padding. 2005's RTM isn't in the pack: its Rich header build is SP1's (50727), so
  SP1 builds both, and a game of 2005-2006 may need RTM (decomp.me's `msvc8.0`). wibo 1.2.0 doesn't run these
  compilers' linkers (`lstrcmpiW`, added to wibo after it), which only the demo's build needs. On a Windows computer
  the pack is untested: there the compilers need their C runtime (msvcr80, msvcr90 through side-by-side manifests,
  msvcr100), which wibo has built in.
- **D, 2012-2013, then 2015.** The same, per generation: SSE2 float code by default from 2012, and one toolset for
  2015's updates.
- **E, whole-program optimisation.** With /GL the linker generates the code for the whole program, so one file's
  object says nothing about the program's bytes. Find out whether some functions can be judged by compiling their
  file with /GL and linking it with /LTCG against stubs; until then a mostly-/GL game isn't started.
- **F, 64-bit.** Read PE32+ programs and delink their relocations (nearly all relative), with the x64 calling
  convention. A 64-bit program lists every function's start and end in its exception directory (`.pdata`), so
  functions come from the program, not Ghidra's guess. Done when a 64-bit demo, then a real game, imports and
  matches.

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

### C++ classes for PowerPC and x86

The class recovery reads MIPS today; Windows PC programs built with RTTI have their classes, vtables, bases and
constructors named from it (done). Left: the fields of PC classes (MSVC: `this` in ecx), the classes of PC programs
built without RTTI (from the vtables constructors store, as on MIPS), and GameCube and Wii (CodeWarrior: the vtable
pointer often isn't at offset 0), with their own instruction readers, in fork mode.

### A modding-ready check for the export

The export rebuilds the game byte for byte; mods need code that can move. Build a second copy with padding inserted
and report every address that is still hard-coded ("93% movable, 41 addresses left"). The matching build stays as
it is.

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
- macOS.
