# Porting a finished decomp (plan)

Status: phase 0, the readiness report, is built (ROM projects; N64 with its kit); phase 1's judge and tools are
built (N64); phase 3's first step, compiling the game's C for a PC, is built (N64), and so is phase 4's task with its
two-way judge (N64); phase 2's asset map is written as Torch's config, one BLOB per resource, and extracted and checked by the
pinned Torch (N64, macOS and Linux); the rest is planned. ROADMAP.md, "Ports: a native build of a finished game", tracks it.

A matched, polished decomp rebuilds the game for its console. People finish a decomp to read, mod and port the
game, and today decompdecomp stops at the export. This plan adds the next step: a **port**, the same C built for
Windows, Linux and macOS, with the console's hardware replaced by a library and the game's data extracted from the
user's own copy. N64 comes first, on [libultraship](https://github.com/Kenix3/libultraship) (libultra's API, a
renderer that runs the game's display lists, input, saves, a resource archive) and
[Torch](https://github.com/HarbourMasters/Torch) (extracts a ROM's assets into that archive from a YAML map). Both
are MIT-licensed and are what the community's N64 ports are built on.

## The rule that makes it safe

Port work never weakens the judge. A change made for the port is kept only when **the console build still matches
byte for byte and the port still compiles**, the way polishing keeps a rename only when the function still matches.
What can't be written so the original compiler makes the same bytes goes behind `#ifdef PORT`, and the matching
build never sees it. The port is a second build of the same tree, never a fork of it.

Game data is never published. The port's repository holds code and a Torch map (offsets and types, no bytes); each
user's ROM becomes their own archive on their machine, as with the existing PC ports.

## Where a finished game stands

The first game to try it on is an N64 rally game: 2,121 functions, 2,093 in C, 28 assembly, the byte-identical
cartridge rebuilt by the export. What a port still needs from it:

| | Today | What the port needs |
| :-- | :-- | :-- |
| Code | 28 functions in assembly | Most are libultra (`osInvalDCache`, `osDisableInt`...), which libultraship replaces. The game's own (about a dozen) need C behind `#ifdef PORT`; it doesn't have to match. |
| Graphics | Nintendo's F3DEX 2.05 (fifo) and S2DEX microcode, no custom ucode | libultraship's renderer interprets F3DEX display lists. Its S2DEX coverage is to be checked first (open question 1). |
| Audio | libultra's synthesizer (42 `al*` functions) | The game's audio code compiles natively; the RSP audio task needs a CPU mixer that runs the ABI commands (`aADPCMdec`, `aResample`, `aEnvMixer`...). |
| Data | 1,585 `D_xxxxxxxx` symbols referenced from C, data as labelled assembly; 11 MB of the ROM in 12 unsplit `.bin` blobs | Every piece of data as C or as an extracted asset, reached by name, not by address. The biggest job. |
| Names | 962 matched functions still `func_...` | Helps people working on the port; not required. |

A game that uses custom microcode needs its renderer support written first; the readiness report says so.

## Phases

Each phase is useful on its own, and each is a prerequisite for the next.

### 0. Port readiness report

Built. **Port readiness** on a ROM project's Overview (opened, it reads; it never runs on its own), the
`port_readiness` tool for agents and `GET /api/port-readiness` (src/decompnet/port/readiness.py). It reads the
project and changes nothing, with no compiler run: under a second on a 2,121-function N64 game. It counts:
- functions with and without C; library code the kit replaces and library code compiled as it is. Library code is
  only what the app has proven (app/sdk_identity.py); a function merely named like libultra is listed as unproven
  and counts as the game's own;
- the graphics microcode the ROM signs (`RSP Gfx ucode ...`) and whether the kit renders it; the audio library;
- data the C reaches by a symbol that exists only as an address; addresses written in C as numbers;
- the ROM's bytes outside its code, and which ROM Explorer's saved scan recognised (it reads the scan, never starts
  one).

Still to add: pointers stored in 32-bit integers, and the moved-build count of the modding-ready check. Consoles
without a kit get the counts that don't need one; projects in project mode aren't read yet.

### 1. Data migration, judged

Data symbols become units next to functions. An agent writes a symbol's data as C (an array, a struct with the
layouts `guess_struct` and the shared header already know, a table of pointers to named symbols); it's kept only when
it holds the game's bytes. Pointers into code and data become symbol references, which is what lets the port, and
mods, move them.

Built:
- **The data judge** (`rom/data_match.py`): one definition, placed at its symbol's address by the function judge's
  linker, its pointers resolved and the strings it points at placed where the game's pointers point. It must cover
  the whole datum: up to the next place the game names or its code refers to (each function's stored relocations,
  read from the ROM), short of it by no more than the next datum's alignment in zero bytes. Data the game keeps
  initialised needs an initialiser, zeros too, or the compiler moves it to `.bss`.
- **Where the data is** (`app/data_units.py`): splits record code segments' text; the data the compiler put after it
  runs to where `.bss` starts, which the console reads from the program (`Console.bss_start`; N64: the boot stub
  that clears it). The function judge doesn't read this region yet, so own data in matched functions there is
  still unchecked.
- **Units** keyed by address in the project's database (`data_units`); a matched unit is replaced only by C that
  matches. Tools `get_data_unit` and `compile_data`; `GET/POST /api/data-unit`; the readiness report counts data in C.

On a finished 2,121-function N64 game: its 0xCCC0 bytes of data found after its last code segment; drafts of the 129
zero-filled data and 19 tables of function pointers matched, 38 `.bss` symbols too.

- **A task for agents** (`app/data_agents.py`): **Start agents → Write data as C**, offered on ROM projects once
  polishing is. The queue is every place in the initialised data the game's code refers to, in address order,
  fewest tries first, but places inside a unit already in C (a struct's fields). `claim_data` leases one unit (it
  comes back on its own after 30 minutes without a compile), `compile_data` judges it and a match frees it,
  `release_data` gives it back with a note the next try reads. Data agents see only the data tools and read-only
  helpers, compile only the unit they hold, and their compiles count as their session's work.

Driven through the agents' own tool calls on a copy of a finished N64 game: 80 claims, 79 matched with mechanical
drafts, none handed out twice. Mechanical drafts (raw `u32` words, pointers cast to `u32`) match the bytes but are
what a 64-bit port must undo; the prompt asks agents for the types the code uses, which the judge can't check.

- **Machine drafts** (`app/data_drafts.py`): Port readiness → **Write data as C from the code**, a background job
  with no agents and no tokens. Each datum's type comes from the game's code: every load and store of it (the
  facts guess_struct uses, `Arch.field_accesses`) says each field's width, sign or float, and which fields are
  pointers; a word holding a known symbol's address is a pointer to it. A draft is written only when the code
  explains every byte (a field it uses, a pointer to a known symbol, or zero padding before an aligned field); a
  datum read two ways, or with bytes nothing explains, goes to the data agents, whose brief now lists the fields
  the code uses. Drafts are judged like agents' C.

- **Saved and exported** (`app/data_saves.py`, `rom/export.py` `_data_in_c`): matched data goes in the progress
  folder as `data.json` (the index) and `data/<name>.c`, written when a unit matches and by every save, owned by
  progress commits and allowed by rebuildable repositories' `.gitignore`; a restore judges each one again. The
  export turns the raw segment holding the initialised data into a segment placed at its address, builds each
  matched datum from its C (recompiled, judged again, and only when its object holds the datum alone: strings of
  its own would land after it), and fills the other bytes from the user's copy, so the whole-image check proves
  the data's C too.

Still to do: an LLM agent run on a real game; data the split wrote as assembly (not a raw segment) built from C;
data whose C brings strings of its own placed in the export; typed drafts; flagging pointers stored as integers (phase 0's open count); `.bss`; programs whose data
isn't after their last code segment (overlays) and consoles other than the N64 (a `bss_start` reader each).

### 2. Asset map, written as a Torch config

ROM Explorer already traces PI DMA loaders into source groups, decodes texture banks and finds sounds, and asset
agents propose regions with validation (docs/ROM_EXPLORER.md). This phase classifies each region into one of
Torch's asset types (texture, palette, vertices, display list, sound bank, sequence, sample table, blob), names it
after the symbol that loads it, and writes the game's Torch YAML, keyed by the ROM's SHA-1. A region is "mapped"
when Torch extracts it and the extracted asset round-trips to the same bytes. The map is publishable: no game
bytes are in it.

Built first (`port/asset_map.py`, the N64 kit's `torch_files`): Port readiness → **Write asset map** puts every
byte outside the code segments in one resource, grouped from ROM Explorer's scan: an archive the game unpacks
itself, audio, or other data. Each is a Torch `BLOB` for now, and the map is checked to cover the ROM exactly once
with the code, the N64's 0x1000 boot bytes and the trailing fill. Typing comes next: a game that stores its data
packed (a finished N64 game keeps it all in one ZIP its own code inflates, which Torch can't) keeps the archive
whole and unpacks it at run time as it did on the console, so its textures never become Torch textures; loose
textures, palettes, vertices and display lists will. **Extract with Torch** then runs
the pinned Torch (built from its source and its libraries, each pinned by commit and checksum, with a pinned CMake)
on the map and the user's ROM, and checks every resource it made against the ROM bytes it names: that is
"mapped". A finished N64 game's 11 resources all came out as the ROM's bytes. The archive (`.o2r`) is the game's
own data, so it stays in the project's `build/port`, out of git.

### 3. Port export

A sibling of today's Export: a CMake project that builds the game natively. It holds:
- the game's C (the export's tree, SDK functions left out where the kit provides them);
- the kit, pinned by commit and checksum in `toolchains.toml` like every other tool: libultraship and Torch;
- generated glue: the main loop in place of the game's threads and message queues (scheduler, VI retrace), input
  through libultraship's controllers, EEPROM and Controller Pak saves as files, `osPiStartDma` from ROM turned into
  resource loads from the archive, the audio mixer;
- a first-run step that runs Torch on the user's ROM to make the archive.

Built first: **Compile for PC** (Port readiness; `port/pc_compile.py`), which compiles every function the port
keeps, on its own, with the project's shared header, by a current clang for 64-bit x86 Linux (the pinned Zig, whose
clang brings its own headers: the same result on every computer). Nothing is linked or run. Each function's errors
and pointer-size warnings are kept in the project's database, sorted into what a port fixes (MIPS `asm`, calls
with no declaration, integers passed as pointers, the C's own size checks, the shared header, pointers cast to
32-bit integers), and the readiness report counts them. A finished N64 game's first run: 101 of 2,005 functions
fail, 397 more cast pointers to 32-bit integers; the 32-bit build failed the same 101, so the pointer casts are
what 64-bit adds. The game's C declares libultra itself (its shared header), so libultraship's headers aren't
needed yet; they come with the export's glue.

Then what a link would refuse: Compile for PC keeps each object's defined and needed symbols, and Port readiness
lists what's needed and defined nowhere, by what fixes it, and what's defined twice (`port/pc_link.py`). On a
finished N64 game: 1,424 data symbols still only addresses in assembly, 43 calls to functions by an automatic name
they lost when polishing renamed them, 14 functions in assembly, 10 libultra functions the kit provides, and 4
helpers defined twice. Linking against the real libultraship comes after those.

Of the data symbols, nearly all are memory the game zeroes at boot, with no bytes in the ROM. Compile for PC
defines them (`port/zeroed.py`, files in the project's `port/src`) with the types the game's C declares, sized
by the PC compiler; what's left of the data group is data with bytes in the ROM (phase 1's data units), ROM
offsets the asset map covers, and libultra's low-memory globals the kit provides. Calls by a function's old name are put right by
**Fix calls by old names** (`port/renamed_calls.py`): the new name in the callers, each judged on the console and
the PC. Library functions are sorted by the kit: libultraship defines part of libultra
(46 names at the commit Torch pins), the host's C library the rest of libc, the game's own C compiles libultra's
math, and what remains of libultra (threads, RSP tasks, the cartridge and Controller Pak internals) is glue the port
supplies; the link check lists that glue as its own group.

The game's threads stay threads (`consoles/n64/port_glue/threads.c`): libultraship's queues never wait, so the glue
replaces them with queues that do and runs each game thread on a host thread, one at a time by priority, as the N64
does. That spares rewriting the game's threads into a main loop by hand. What it can't do yet is preempt a thread
that never waits; a game that spins on a flag another thread sets would stop, and the glue counts the times a woken
higher-priority thread had to wait. Which runtime a port uses (libultraship with this glue, or N64ModernRuntime with
RT64) is to be the user's choice per game (ROADMAP.md, "A choice of runtime for a port").

ROM offsets the game uses as numbers (where an archive starts in the cartridge) and the values the N64's boot leaves
in low memory for libultra are defined by Compile for PC too (`port/fixed_symbols.py`): offsets as symbols at those
addresses, boot values with what the boot would leave, the TV system from the game code's country.

### 4. Port fixes, an agent task judged twice

Like **Polish matched code**, a task agents take from **Start agents**, offered once a game is at 100% and its
data is migrated. The brief lists what blocks the port build in that function, from the compiler's errors and
static checks:
- pointers held in `u32`/`s32` and struct fields sized for 32-bit pointers;
- `Gfx` commands built by hand as two words (a `Gfx` is larger on a 64-bit host);
- byte order where game code parses raw ROM data itself, rather than through an extracted asset;
- segmented addresses and `osVirtualToPhysical`;
- undefined behaviour the MIPS build gets away with (uninitialised locals, out-of-bounds reads, signed overflow);
- K&R-era C that a current compiler refuses (implicit declarations, implicit `int`).

The judge runs both builds; a fix that can't keep the console build matching is accepted only inside
`#ifdef PORT`.

Built (`app/port_agents.py`, `port/port_fix.py`): **Start agents → Fix C for the PC** works through what Compile for
PC listed, the functions that don't compile first. `check_for_pc` shows the PC's verdict of a try alone;
`fix_for_pc` keeps a fix only when the function judge still finds the game's bytes and the PC build (PORT defined)
is no worse than before on errors and pointer-size warnings and better on one: a big function is fixed a group of
lines at a time, and the agent keeps it until nothing is left. A fix the header alone made (a prototype added with
`edit_context`) is kept by submitting the function's C unchanged. The first LLM run showed what agents need told:
when a pointer type changes the old compiler's operand order or registers, the PC's lines go behind `#ifdef PORT`
with the original under `#else` instead of more tries; display-list words that hold addresses are `uintptr_t` on
the PC (a `Gfx` of its own in the header, behind PORT); gcc's `__builtin_next_arg` varargs become `<stdarg.h>`,
callee first (`app/guide/port.md`). The PC side judges the one function with the header as
it is; a prototype an agent adds to the header for it is judged with it, and the next full check shows what it does
to the rest. Not caught by compiling yet: `Gfx` commands built by hand as two words, which compile for a PC and
are wrong there. Building the port 32-bit first (Linux, Windows) defers most pointer fixes; macOS needs 64-bit.

### 5. Play, and check that it runs

A **Play port** button builds and starts it. A headless check runs N frames: it starts, doesn't crash, and the
screen isn't one colour. Later: compare frames with an emulator running the original at the same inputs.

## Where it lives in the code

- `src/decompnet/port/`: what every console shares (readiness report, data claims, the port-fix task and its
  double judge, the export driver, Play).
- A connector part, `Console.port_kit`, one line in `consoles/__init__.py`: the library, its asset extractor, the
  SDK functions it replaces and the glue templates. N64's is in `consoles/n64/port/`.
- PS1 has a candidate kit: PsyCross reimplements the PlayStation SDK over OpenGL and SDL and carried a community
  PC port of a PS1 driving game. Every other console gets a "—" in the ROADMAP table and an item, until a kit exists.
- Tests: the N64 demo game (`projects/demo-n64`) gets a port build in CI-skippable tests, like `tests/test_consoles.py`.

## Open questions

1. **S2DEX.** Does libultraship's renderer cover the S2DEX commands the game uses? Try one extracted 2D display list
   before anything else.
2. **Audio mixer.** Which CPU mixer for libultra's audio ABI, under which licence? sm64-port's runs the same commands;
   check its licence before bundling, or write one.
3. **First version's scope.** Readiness report and Port export, with fixes done by hand, before the agent task?
4. **Hand-port first.** Port one game's boot, title screen and one track by hand before building the general feature,
   and order the phases by what breaks.
