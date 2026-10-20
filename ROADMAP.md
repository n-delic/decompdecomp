# Roadmap

What's planned for decompdecomp, roughly in order, and why. Every item keeps the rule that matters most: a match is
byte-identical after linking (ROM mode) or objdiff 100% (fork mode). Nothing here accepts code that only does the
same thing.

Want to work on one? Open an issue first, so two people don't build the same thing. CONTRIBUTING.md has the rest.

## What works where

Each feature and the platforms it covers today. A — is a gap: pick one up (the notes under the table say where to
start). Keep this table true when a feature ships or reaches another platform. Each column's code is its console's
folder, `src/decompnet/consoles/<id>/`; a new console adds a column (CONTRIBUTING.md, "Consoles").

| Feature | N64 | PS2 | GameCube · Wii | Windows PC | Public decomps |
| :-- | :-: | :-: | :-: | :-: | :-: |
| Status | recommended | beta | beta | beta | the project's own |
| Start from your own copy | ✓ | ✓ | ✓ | ✓ | fork by address |
| Judge | link · compare | link · compare | objdiff | objdiff | objdiff |
| Machine drafts (m2c, Ghidra for x86) | ✓ | ✓ | ✓ | ✓ Ghidra | by architecture |
| Autopilot: free matches from drafts | ✓ | ✓ | ✓ | — | — |
| Drafts follow the header, made in the background; `redraft` | ✓ | ✓ | — | — | — |
| C++ methods drafted as `Class::method` | ✓ | ✓ | — | — | — |
| Twins: free matches from identical code | ✓ | ✓ | ✓ | ✓ | ✓ |
| Permuter on near misses | ✓ | ✓ | ✓ | ✓ | ✓ |
| Hand-written assembly set aside | ✓ | ✓ | ✓ | — | MIPS, PowerPC |
| Kernel stubs set aside and named | — | ✓ | — | — | — |
| Function-end reports (`report_boundary`) | ✓ | ✓ | — | — | — |
| Compiler ranges and suggestions | ✓ | ✓ | the project's build | the project's build | the project's build |
| Flag search (`search_flags`) | ✓ | ✓ | the project's build | the project's build | the project's build |
| Compiler map and the judge's survey | ✓ | ✓ | — | — | — |
| Range checks: a range applies after its neighbours confirm it | ✓ | ✓ | — | — | — |
| C++ classes from the code (`get_class`) | ✓ | ✓ | — | — | — |
| Struct layouts from loads and stores (`guess_struct`) | ✓ | ✓ | — | — | — |
| The game's own debug symbols: names, types, prototypes | — | ✓ symbol table, STABS | — | — | — |
| Twins across your games | ✓ | ✓ | — | — | — |
| Shared knowledge (two models agree) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Polish matched code (still matching) | ✓ | ✓ | — | — | — |
| Save and restore (`progress/`, Open a saved project) | ✓ | ✓ | the project's git | the project's git | the project's git |
| Rebuildable export | ✓ | ✓ | the project | — | the project |
| Rooms: decompile together | ✓ | ✓ | ✓ | ✓ | ✓ |

Where to start on a gap:
- **PowerPC (GameCube, Wii) and x86 (PC)**: class recovery and the compiler map work on a project's objects in fork
  mode, not on a ROM; the readers are the part to write (CodeWarrior's vtable pointer is often not at offset 0).
- **Windows PC**: autopilot needs drafts that compile (Ghidra's output doesn't, as it is); the export needs a linker
  story (the project is delinked COFF objects).
- **Function-end reports** cut MIPS functions (`engine._cut`).
- **Struct layouts** read MIPS loads and stores (`arch/mips_fields.py`); another processor adds its reader as its
  `Arch.field_accesses`, and structs.py does the rest. Still open on MIPS: the PS2's fifth to eighth arguments
  (t0-t3), and a pointer into the middle of a struct handed to a callee (`&this->pos`).
- **Polish, saves, twins across games** work on ROM projects (`app/readable.py`, `app/saves.py`, `app/twins.py`).
  A project-mode console's work is its git repository; polishing there needs objdiff to judge a rewrite.

## Recently done

- **Model routing.** Each model's share of its claims that matched, in the project, tells the models at work
  apart (on an N64 game free models matched 14-25% of what they claimed, stronger ones 42-77%). A model well behind
  the strongest one at work leaves functions given up on three times to it, and the strongest takes those first;
  near misses already go to the permuter. Nothing changes until two models have 20 claims each.
- **Helpers' effort.** The launcher's Effort, opencode's until now, is every helper's: Claude Code's (`--effort`,
  Low to Max with Extra high), Codex's (`model_reasoning_effort`, Minimal to Extra high) and the API helper's
  (OpenAI's `reasoning_effort`, Minimal to High, for servers whose models reason). A model at another effort is a
  helper of its own (`claude-opus-xhigh-1`), so its matches and spend are counted apart.

- **Rooms that say what's happening** (1.0.4-alpha). A guest waiting for the host sees "Waiting for the host to let
  you in" over the project, not an empty one and a red "Room offline"; then "Getting the host's work", counting down.
  A new guest starts at the room's newest event instead of replaying its history into the feed and notifications,
  and one that was away catches up a page at a time (the sync started from nothing at every start). Each computer
  has a colour on its agents' names, the same on every screen, and the Agents tab lists what the room's other
  computers' agents are on. A git project's join waits to be let in (with approval on, it failed), and a declined
  or removed guest is told so.
- **A standalone app.** Its own window (GTK and WebKitGTK) and a tray icon, so closing the window doesn't stop
  helper agents; **Quit** ends everything the app started. Only helpers the app starts work on projects: the old
  `decomp mcp` bridge is gone, and the first start removes its registrations from your agents. A development copy is
  a separate app (`~/Decomp-dev`) that never touches the installed one.
- **Helpers that restart by themselves**, with backoff; on a usage limit they wait until it resets. The API helper
  (any model with an API key) is the main helper: the app runs its whole loop.
- **One notification centre** (the bell) for every project, and **Report a problem** on every page.
- **Save and restore** (ROM projects): the work as plain files in `progress/`, every few minutes; **Open a saved
  project** rebuilds it on another computer from your own copy, and judges every function again.
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
- **Consoles as packages**: one folder per console (`consoles/<id>/`), a template and a conformance test. PS1, GBA
  and DS are public decomps only now; the git history keeps their code.
- **Linux packages**: a `.deb`, an `.rpm` and an AppImage with their own Python, built for each release.
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

### How much usage each agent has left

A window that shows, for each agent you use, what's left of its limits: the 5-hour and weekly limits of Claude Code
and Codex, and a monthly limit or the credit left on an API key where the provider says it. Helpers already wait for a
usage limit to reset; this shows it before they hit it, so you can pick which agents to start.

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

### A shareable progress card

An image with % matched, functions and platform, for READMEs and posts. No game code in it.

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
