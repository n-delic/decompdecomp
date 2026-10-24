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
| C++ methods drafted as `Class::method` | ✓ | ✓ | ✓ | — | — | — | — |
| Twins: free matches from identical code | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Permuter on near misses | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Hand-written assembly set aside | ✓ | ✓ | ✓ | ✓ | — | BIOS call stubs, pieces of functions | MIPS, PowerPC, ARM's BIOS stubs and pieces of functions |
| Pieces of functions joined to their function | — | — | — | — | — | ✓ (`recover.merge_pieces`) | — |
| Agents' verdict "no C makes this" (3 agents of 2 models: set aside, red in the map) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Check again (and before helpers start, once after each update) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Kernel stubs set aside and named | — | set aside (BIOS calls) | ✓ | — | — | — | — |
| Function-end reports (`report_boundary`) | ✓ | ✓ | ✓ | — | — | — | — |
| Compiler ranges and suggestions | ✓ | ✓ | ✓ | from scratch: per source file (`try_compiler`, Settings); else the project's build | the project's build | per source file (`try_compiler`, Settings) | the project's build |
| Flag search (`search_flags`) | ✓ | ✓ | ✓ | the project's build | the project's build | the project's build | the project's build |
| Compiler map and the judge's survey | ✓ | ✓ | ✓ | — | — | — | — |
| Range checks: a range applies after its neighbours confirm it | ✓ | ✓ | ✓ | — | — | — | — |
| C++ classes from the code (`get_class`) | ✓ | ✓ | ✓ | — | — | — | — |
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
  mode, not on a ROM; the readers are the part to write (CodeWarrior's vtable pointer is often not at offset 0).
- **Windows PC**: autopilot needs drafts that compile (Ghidra's output doesn't, as it is); the export needs a linker
  story (the project is delinked COFF objects).
- **Function-end reports** cut MIPS functions (`engine._cut`).
- **Struct layouts** read MIPS loads and stores (`arch/mips_fields.py`); another processor adds its reader as its
  `Arch.field_accesses`, and structs.py does the rest. Still open on MIPS: the PS2's fifth to eighth arguments
  (t0-t3), and a pointer into the middle of a struct handed to a callee (`&this->pos`).
- **Twins across games** and **restoring a save** work on ROM projects (`app/twins.py`, `app/saves.py`). In project
  mode a project's matched C is its git repository; what's missing there is matching identical code across your
  games (their objects, not a ROM, hold it) and opening a save's best attempts in a new copy of the project.

## Recently done

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
- **Bring saved ROM progress into a room.** When joining with a game already imported, saved matches the host has
  not matched yet are submitted for the host to verify. Better unfinished attempts are shared when both projects
  use the same compiler settings; functions whose layout or name differs are skipped.
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

The class recovery reads MIPS today. GameCube and Wii (CodeWarrior: the vtable pointer often isn't at offset 0) and
Windows PC (MSVC: `this` in ecx) work the same way with their own instruction readers, in fork mode.

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
