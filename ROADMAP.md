# Roadmap

What's planned for decompdecomp, roughly in order, and why. Every item keeps the rule that matters most: a match is
byte-identical after linking (ROM mode) or objdiff 100% (fork mode). Nothing here accepts code that only does the
same thing.

Want to work on one? Open an issue first, so two people don't build the same thing. CONTRIBUTING.md has the rest.

## What works where

Each feature and the platforms it covers today. A — is a gap: pick one up (the notes under the table say where to
start). Keep this table true when a feature ships or reaches another platform.

| Feature | N64 | PS1 | PS2 | GBA | GameCube · Wii | DS | Windows PC | Public decomps |
| :-- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Start from your own copy | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | fork by address |
| Judge | link · compare | link · compare | link · compare | link · compare | objdiff | objdiff | objdiff | objdiff |
| Machine drafts (m2c, Ghidra for x86) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ Ghidra | by architecture |
| Autopilot: free matches from drafts | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| Twins: free matches from identical code | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Permuter on near misses | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Hand-written assembly set aside | ✓ | ✓ | ✓ | — | ✓ | — | — | MIPS, PowerPC |
| Kernel and BIOS stubs set aside | — | ✓ | ✓ | — | — | — | — | — |
| Function-end reports (`report_boundary`) | ✓ | ✓ | ✓ | — | — | — | — | — |
| Compiler ranges and suggestions | ✓ | ✓ | ✓ | ✓ | the project's build | the project's build | the project's build | the project's build |
| Compiler map and the judge's survey | ✓ | ✓ | ✓ | — | — | — | — | — |
| Range checks: a range applies after its neighbours confirm it | ✓ | ✓ | ✓ | — | — | — | — | — |
| C++ classes from the code (`get_class`) | ✓ | ✓ | ✓ | — | — | — | — | — |
| The game's own debug symbols: names, types, prototypes | — | — | ✓ symbol table, STABS | — | — | — | — | — |
| Rebuildable export | ✓ | ✓ | ✓ | ✓ | the project | the project | — | the project |
| Rooms: decompile together | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Where to start on a gap:
- **ARM (GBA, DS)**: assembly detection is `handwritten.py` (`mips_reason` and `ppc_reason` show the shape); the
  compiler map and class recovery read MIPS in `compilermap.quirks` and `classes.scan`, and want an ARM reader.
- **PowerPC (GameCube, Wii) and x86 (PC)**: class recovery and the compiler map work on a project's objects in fork
  mode, not on a ROM; the readers are the part to write (CodeWarrior's vtable pointer is often not at offset 0).
- **Windows PC**: autopilot needs drafts that compile (Ghidra's output doesn't, as it is); the export needs a linker
  story (the project is delinked COFF objects).
- **Function-end reports** cut MIPS functions (`engine._cut`); GBA has its own splitter (`_split_gba`).

## Recently done

- **Range checks** (MIPS ROM projects). An agent's "this range was built by another compiler" is a hypothesis: the
  judge tests the functions next to it (their matches, agents' attempts, the survey, habits), and where nothing
  settles it the nearest neighbour goes to an agent as a task. A range applies once a second function matches only
  with its compiler; refuted neighbours on both sides make it a quirk (dismissed, with a note on the function). The
  applied ranges can be checked again the same way (a report). On GTA San Andreas's 72 ranges: 12 confirmed, about
  25 resting on one function, about 36 with no function whose C needs their compiler.

- **Compiler map** (MIPS: PlayStation 2, PlayStation, N64): which compiler built each part of the game. Measured on
  GTA San Andreas, compilers' habits only rule compilers out (-G0 code never reads through `$gp`; its CodeWarrior
  code never uses branch-likely), so what rules a compiler in is the judge: the survey builds agents' C for unmatched
  functions with each compiler the project uses, and C that matches with another compiler (and not today's)
  confirms it. Confirmations spread over their neighbours (one object file is one compiler) up to the furthest
  evidence; a run big enough becomes a suggestion, checked against every matched function. The ROM map shows it
  (Compiler view); agents read it with `compiler_map` and in `get_function`.
- **C++ classes from the game's code** (MIPS: PlayStation 2, PlayStation, N64). Constructors store their class's
  vtable into the object; from those stores decompdecomp finds each class, its virtual functions, its base class and
  every field its functions touch (offset, type). Agents read them with `get_class`; `get_function` names the fields
  of `this` a function uses. On GTA San Andreas: 507 classes, and the recovered entity layout (position at +4, matrix
  pointer at +0x14, model index at +0x22) is the one found by hand. Why: functions with virtual calls matched 7% of
  the time against 62% for the rest, because agents guessed the layouts.
- **Linux packages**: a `.deb` and an AppImage with their own Python, built for each release.
- **Its own window and a tray icon**, so closing the window doesn't stop helper agents.

## Next

### Twins across your games

Twins already give free matches for code a game has more than once. PlayStation 2 games share RenderWare, Sony's
libraries and libc, so a library function matched in one of your games should be tried in all your others: a
fingerprint index shared by your projects, the same trial, the same judge. No agent time; nothing leaves your
computer.

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

- Windows as a host again, the best way found after the rewrite (docs/REWRITE.md §1).
- macOS.
