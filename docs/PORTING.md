# Porting a finished decomp (plan)

Status: phase 0, the readiness report, is built (ROM projects; N64 with its kit); phase 1's judge and tools are
built (N64); the rest is planned. ROADMAP.md, "Ports: a native build of a finished game", tracks it.

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

Still to do: an agent task for it (claims, a brief, a launch option, like polishing); the export building matched
data from its C in place of the assembly; typed drafts; programs whose data isn't after their last code segment
(overlays) and consoles other than the N64 (a `bss_start` reader each).

### 2. Asset map, written as a Torch config

ROM Explorer already traces PI DMA loaders into source groups, decodes texture banks and finds sounds, and asset
agents propose regions with validation (docs/ROM_EXPLORER.md). This phase classifies each region into one of
Torch's asset types (texture, palette, vertices, display list, sound bank, sequence, sample table, blob), names it
after the symbol that loads it, and writes the game's Torch YAML, keyed by the ROM's SHA-1. A region is "mapped"
when Torch extracts it and the extracted asset round-trips to the same bytes. The map is publishable: no game
bytes are in it.

### 3. Port export

A sibling of today's Export: a CMake project that builds the game natively. It holds:
- the game's C (the export's tree, SDK functions left out where the kit provides them);
- the kit, pinned by commit and checksum in `toolchains.toml` like every other tool: libultraship and Torch;
- generated glue: the main loop in place of the game's threads and message queues (scheduler, VI retrace), input
  through libultraship's controllers, EEPROM and Controller Pak saves as files, `osPiStartDma` from ROM turned into
  resource loads from the archive, the audio mixer;
- a first-run step that runs Torch on the user's ROM to make the archive.

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
`#ifdef PORT`. Building the port 32-bit first (Linux, Windows) defers most pointer fixes; macOS needs 64-bit.

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
