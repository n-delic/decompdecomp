# Porting a finished decomp (plan)

Status: phase 0, the readiness report, is built for ROM and project-mode games on every console (N64 with its kit); phase 1's judge and tools are
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

Built. The **Porting** tab of any project (opened, it reads; it never runs on its own) shows it as a checklist: each
step with how far along it is (in C, library code, data as C, addresses, compiled and linked for a PC, assets, Play)
and the button that does it, and the
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
without a kit get the counts that don't need one.

Project-mode reports read the unit's current C/C++ files, including source includes and C++ methods. Old objdiff
files without source paths can use a unique source file named exactly after the unit; inferred paths are labelled.
Matched objects without a readable body stay separate from C: an object may match through assembly. The report
never runs setup, builds, extracts assets or changes source, matches or project settings.

A project-mode report is not a runnable port. The app still needs adapters for the project's headers, compiler
flags, translation units, assets and runtime build before Compile for PC, Play and Export can run there. The
checklist names these gaps, leaves unknown data coverage unchecked and offers only supported actions. This also
applies to public N64 decomps: their source-tree builds differ from decompdecomp's ROM saves. Their source is not
silently converted to the ROM-save layout. Missing source, missing setup and unknown consoles still get reports.

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

- **Machine drafts** (`app/data_drafts.py`): Porting → **Write data as C from the code**, a background job
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

Built first (`port/asset_map.py`, the N64 kit's `torch_files`): Porting → **Write asset map** puts every
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

Built first: **Compile for PC** (Porting; `port/pc_compile.py`), which compiles every function the port
keeps, on its own, with the project's shared header, by a current clang for 64-bit x86 Linux (the pinned Zig, whose
clang brings its own headers: the same result on every computer). Nothing is linked or run. Each function's errors
and pointer-size warnings are kept in the project's database, sorted into what a port fixes (MIPS `asm`, calls
with no declaration, integers passed as pointers, the C's own size checks, the shared header, pointers cast to
32-bit integers), and the readiness report counts them. A finished N64 game's first run: 101 of 2,005 functions
fail, 397 more cast pointers to 32-bit integers; the 32-bit build failed the same 101, so the pointer casts are
what 64-bit adds. The game's C declares libultra itself (its shared header), so libultraship's headers aren't
needed yet; they come with the export's glue.

Then what a link would refuse: Compile for PC keeps each object's defined and needed symbols, and Porting
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

The Porting picker separates **N64 compatibility (32-bit game memory)** from **Direct 64-bit C port**. The former
uses WebAssembly in the browser and wasm2c for desktop builds. It can play and export with decompdecomp's runtime
today. **N64ModernRuntime** also offers experimental desktop Play with RT64 on macOS when the adapter is installed.
The direct C mode shows each runtime's outstanding 64-bit port prerequisites.

For a source checkout on a Mac, install full Xcode and its Metal Toolchain in Components, then run
`uv run python tools/build_rt64_adapter.py` and refresh Porting. The script builds pinned public source revisions
and saves the adapter under the decompdecomp toolchains directory. A local macOS bundle can carry those files
when built with `DECOMP_RT64_BUNDLE_DIR`. In Porting, select **N64 compatibility**, then **N64ModernRuntime**,
and click **Play**. The adapter uses N64ModernRuntime's VI state and RT64's Metal renderer. The compatibility host
continues to run game threads, input, audio and saves. It sends RT64 a word-swapped RDRAM snapshot and retains the
software RDP's writes to the game's memory. Decompilation, N64Recomp and MIPS interpretation share the same
execution path as the built-in runtime. Desktop Play is verified on a partially decompiled demo on Apple Silicon.
The adapter forwards the game's VI feature settings, including gamma. Interlaced modes use both fields from
libultra's VI mode table and alternate them at retrace, including when the game keeps the same buffer. It uses the
full scanout from game memory when RT64 has cached only part of the framebuffer selected by the VI. This restored the
missing lower portion of the picture in a matched SDL3 and Metal replay of a retail race.
Broader retail renderer accuracy, RT64 exports, and other desktop platforms remain open.

The host reports an Expansion Pak console (osMemSize 8 MB) to every game: it maps 8 MB of RAM anyway, and a game that
needs the pak shows "N64 Expansion Pak not installed" and goes no further on 4 MB (a 32 MB platform game stayed on that
screen; with 8 MB it plays its intro). A project that was built for 4 MB sets `rom.memory_size` to 4 (or the host's
`PORT_MEMSIZE=4`) and keeps the console it expects.

What RT64 draws can be saved without a screenshot (the game's window can't be captured from outside, and the host's own
`PORT_FRAMES` dumps come from the software renderer, which shows black for games RT64 draws fine and the reverse):
`PORT_RT64_FRAMES=<folder>` (`PORT_RT64_FRAME_EVERY=n`) makes the adapter copy each frame RT64 presented, read back
from its swap chain, into `frame_NNNNN.png` (8-bit RGB, window-sized). It costs nothing when unset. The adapter's draw
hook blits the drawable (its layer is made readable) into a shared buffer that the next frame's hook writes out.
This is the evidence that a game renders under RT64 to a menu: a run of a project through Play with the variable set.

RT64 identifies a graphics microcode by hashing its text and data against a database, and interprets only the ones it
knows. A game's own Fast3D build, or one it switches to for some tasks (a racing game's F3DEX2 with changes, two
retail platformers' second microcode), is unknown to it: RT64 gets no GBI, or one with no commands, and its display-list
loop reads on past the list until the host faults or hangs. A fault inside RT64 is taken by the game's wasm trap
handler, so it looked like a game thread stopping on "out-of-bounds access", with a black picture. The adapter now
leaves such a task to the host's own RDP and RT64 presents the result from game memory ("RT64 does not know the
microcode at ..." is printed once for each); a game whose every task is of that kind (a racing game's title screen)
renders through that path alone. RT64 also indexes its RDRAM with 24-bit masked addresses, so it is given a 16 MB view
(the host's stacks live just above the console's 8 MB) backed by a larger zero-filled reservation: a stray index reads
zeros.

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

### A WebAssembly build: running the game before the port is native

Built for the N64 (`port/wasm/`, `python -m decompnet.port.wasm SAVE`). It's a way to run a finished game on a PC
before phases 1 to 4 are done. The game's C is compiled to WebAssembly (wasm32, so pointers stay 32-bit) and
translated back to C by wasm2c, and the host program runs it. The game's memory is a 4 GB WebAssembly memory read
and written big-endian, with every symbol at its console address:
- `beir.py` pins each name to its address and writes initialised data big-endian;
- `bemem.py` byte-swaps wasm2c's loads and stores;
- the host copies the ROM's image to where the boot puts it.

So data stays as the ROM has it (no phase 1), pointers need no fixes (no phase 4's 64-bit work), and a call through
a pointer finds its function by console address.

The host boots any game as the console does, from the ROM: the boot chip's copy of the 1 MB after the header to the
entry address the header names, then what the game's entry routine at 0x1000 does, read rather than run (its BSS,
zeroed, from its first `sw $zero` and the loop's count or end; its first C function, from its jump, called by
address; host.c `read_boot`). The header's region sets osTvType and the retrace: PAL 50 a second, NTSC and MPAL 60.
The boot chip is known by its boot code's CRC-32 (`boot_chips`): the 6103 and 6106 start the game below the
header's entry address, and the host leaves what each chip's code leaves, which games check against piracy: the part
of the boot code copied into RAM to run from (RAM 0x200 on), the RSP's memories filled (and 6103 or 6105 in the
first word of its instruction memory) and osCicId. The cartridge also reads directly at 0xB0000000. A racing game
missing any of these turned its screen upside down, mirrored its tracks or paused in its world.
A game that pages its code in as it runs (a shooter linking its program at a virtual base and loading 4 KB pages from the
cartridge when they fault) works on the host through a model of the TLB (the 32 entries the game writes, its Wired
register, tlbwr replacing only the slots above it) and of the exception libultra's kernel would deliver: a fetch from a
page an entry covers with the valid bit clear is TLB Invalid, and when the game registered a pager for OS_EVENT_FAULT
the host fills the faulting OSThread, points `__osFaultedThread` at it (an address found in the game's own handler),
posts the fault event and parks the thread until the pager clears the thread's fault flag (`port_tlb_fault`); the
pager's `__osGetTLBHi` and `osUnmapTLB` read and clear entries of that table. The interpreter keeps the virtual address
of code it reached through a window (its program counter is the frame in RAM, which is where the rest of the port finds
functions), so the next page of the code is the next virtual page's frame, and a call made from it returns to a virtual
`$ra`. `PORT_TLB_TRACE=1` prints every entry the game writes.
The hand-written library code most games keep in assembly (`bzero`, `bcopy`, `bcmp`, the TLB, status-register and
interrupt helpers, `osGetCount`) is the host's by its library name: the build renames a call by a function's
automatic name to the name library detection found (`play.library_names`). What only one game needs, such as
functions it names by its own addresses, goes in `consoles/n64/port_wasm/games/<the ROM's SHA-1, 12 digits>.c`,
compiled for that game alone (V-Rally's, where the host was written). A function a game runs with no C stops it
there, named: matched C, or C only the port has (`port/wasm/src/`), lets it go on.
When matching C relies on the console compiler's stack layout or on a value left in a register, an address in the
ROM's `.original` file in that same directory selects N64Recomp or the MIPS interpreter. A port-specific source override can supply portable C.

What the matched C gets away with on the console, the build adapts to:
- arguments left in registers become explicit (`adapt.py`'s thunks, with MIPS register words for the rest);
- one signature per function (`sigs.py`);
- calls by a function's older name go to the function (`renames`).

What it can't adapt goes in the game's save repository, `port/wasm/`:
- `exclude.txt`: matched files the host replaces;
- `src/`: C only the port has;
- `overrides/`: the port's version of a matched file, until its fix is in the matched C behind `#ifdef PORT`.

Library code the port compiles as C (libultra's math and synthesizer: `PortKit.sdk_group` "native") needn't be the
game's own matched C. With libultra source (decompals/ultralib, a proprietary pack the user accepts in Compilers &
tools) installed and the project's library setting on, a function library detection finds as ultralib's own build is
set aside as SDK code, and the build compiles ultralib's C for it (`port/ultralib.py`, `build.py`'s `library_c`):
one function a unit, cut from ultralib's file with its directives and static helpers, built with ultralib's headers
and the defines of the build the game's copy matched. The units go to `port/wasm/build/ultralib/`, which git ignores:
ultralib's C is SGI's and Nintendo's and never part of a save. The game's own matched C for a function wins. The link
check lists these as their own group (`ultralib`).

The game's C is the console's: `PORT` is defined only for a file that needs it in WebAssembly (one using
`<stdarg.h>`, `__builtin_next_arg` or inline assembly), because the native port's `#ifdef PORT` fixes are for a
64-bit PC and some drop what the console needs (a display list's address written as 0, "no meaning on PC").
The other MIPS builtins (the coprocessor 0 registers, the FPU's control register) are answered by the host.

The host is the console's (`PortKit.wasm`; N64: `consoles/n64/port_wasm/host.c`). It implements libultra:
- the game's threads, one at a time by priority, preempted at calls when a woken thread outranks the one running;
- message queues, cartridge DMA, the VI retrace, the controllers (`PORT_INPUT="300:START,306:-,540:A"`: the
  first controller's buttons and stick from a given frame on) and the serial bus the game may drive itself (the
  first controller, no Controller Pak, and a 4 Kbit save EEPROM kept in a file: `PORT_SAVE=path`, else `game.eep`
  in the folder the game runs from, written as the game saves);
- the sound (`audio.c`, `audio_musyx.c`): the port runs libultra ABI 1 and n_audio command lists, and
  MusyX v1 subframe descriptors, in software. The audio interface plays each submitted buffer in the live
  view, can save it with `PORT_AUDIO=file.wav`, and signals the game when a DMA buffer completes. The
  libultra resampler still uses linear interpolation, and n_audio's A_POLEF remains unimplemented.
- the app's **Play in browser** (Porting): builds the game from the project's last save, starts it with
  `PORT_LIVE` on a free port and opens it in a window of its own (the app window's program; the browser where no
  window can open); closing the window, Stop the game or quitting the app ends it.
  Its bar counts the build's steps and each step's files, and Stop cancels the build. A rebuild redoes only what
  changed: each file the build makes keeps a key of what it was made from (its C and the headers that C includes;
  for placing, its IR and its own rows of the signature and address tables; for the host, the wasm2c part), so a
  Save that changed one function recompiles that function and the one wasm2c part holding it.
  Its saves stay in the build folder (`game.eep`);
- **Export** in Porting (`port/wasm/export.py`): select one or more targets, then click **Export selected**.
  Selections are saved per project. One job builds the selected targets in order and shows each successful
  export's folder and instructions. The game is exported
  into the save repository's `port/wasm/export/` (git ignores it: it's built from the game). App, browser and iOS
  exports ask for the player's own ROM. The local Miyoo and Steam Deck exports can include your ROM.
  Each export also adds a GitHub Actions workflow to the project's repository (`port/wasm/workflows.py`) that makes
  the same export again on every published release, never on pull requests or pushes: the browser build
  (`export-web.yml`) is deployed to the repository's GitHub Pages (Settings > Pages > Source: GitHub Actions, once),
  the others (`export-app-<system>.yml` on that system's runner, `export-window-macos.yml`, `export-miyoo.yml`, `export-steamdeck.yml`, `export-ios.yml`)
  are attached to the release. A runner has the save alone, so the export writes what its build took from the
  project into `port/wasm/export.json` (the library code the host provides, library code's own names, the functions
  built from ultralib's C, the game's title and version) and the workflow runs
  `python -m decompnet.port.wasm export SAVE --kind KIND --out FOLDER` with the decompdecomp it was exported with
  (its commit, or its release tag); `--ultralib` installs libultra's source there when the save builds library code
  from it. Both files are committed (those paths alone) and never pushed. What a runner can't make it leaves out:
  the handhelds' packs have no ROM (the player puts theirs in it), and the apps have no C recompiled from the ROM:
  a function with no C yet runs from the player's ROM, interpreted ("Functions with no C yet", below).
  - *Standalone app (opens in your browser)*: the native program with a launcher. On macOS a universal app (`Game.app`,
    macOS 11 and up, for Apple Silicon and Intel Macs alike, whichever kind exported it) that asks for the ROM once, keeps its saves in its own Application Support folder and opens the game
    in the browser; quitting it stops the game. Its program is a small compiled universal launcher that runs
    `Resources/play.sh`, and the bundle has an ad-hoc signature, so a zipped or AirDropped copy stays whole; without an
    Apple Developer ID another Mac still asks once (Control-click > Open, or Privacy & Security > Open Anyway), which
    `How to open on a Mac.txt` beside the app tells the player. On Linux and Windows the program with `play.sh` / `play.bat`
    (they take the ROM's path and remember it).
  - *Standalone app (opens in its own Mac window via SDL3)*: the game in an SDL3 window instead of the browser. The app is the
    game itself (`Contents/MacOS/game`), with no script and no AppleScript: opened with no ROM named, it reads
    `Contents/Resources/port.env` (`PORT_WINDOW`, `PORT_TITLE`, `PORT_APP`), keeps its saves, the ROM's path and its
    log in `~/Library/Application Support/<PORT_APP>`, and asks for the ROM in SDL3's own file dialog, again until the
    file is an N64 ROM. SDL3 is built once from its pinned source (toolchains.toml `sdl3`) for Apple Silicon and Intel
    Macs and travels in `Contents/Frameworks` with its licence. Its name and icon are the app's in the Dock; Cmd-Q
    quits. A racing game, opened as Finder opens it, plays in its window as the app itself.
  - *For iPhone / iPad (Xcode project)*: the browser port bundled in an offline iOS app
    (`port/wasm/ios.py`). Open `Game.xcodeproj` on a Mac with Xcode, select the Game target's Signing & Capabilities,
    choose your Apple account's team and a unique Bundle Identifier, then select your connected device and Run.
    Enable Developer Mode on the device when asked. The export includes a README with these steps; it does not
    sign an IPA or submit anything to Apple. iOS/iPadOS 16.4 or later; iPhone and iPad orientations supported.
    The app's WKWebView loads only its bundled port through a loopback HTTP server (no computer or internet
    connection needed after installation). The player chooses their ROM from Files; its remembered ROM and
    saves stay on the device. Touch input has an analog stick, D-pad, A/B/Z, Start, shoulders and C buttons;
    connected gamepads use the browser port's existing support. Tap Sound on for audio. Compatibility and speed
    depend on the current port and device; unfinished code or unsupported hardware remains unfinished.
    The wrapper compiles against the iPhone SDK; a physical-device playthrough remains unverified. The GitHub
    workflow attaches the unsigned project as a tar archive, ready to open and sign in Xcode.
  - *For a web browser*: the browser build (`port/wasm/web.py`), a folder with `index.html` for any static web
    server (browsers load WebAssembly only over http). It runs the game's WebAssembly itself, so what wasm2c's C
    was made to do is done to each function's IR instead (`webir.py`): every load and store through big-endian
    helpers (`web/be.c`, uncached addresses through the host), each variadic call's extra arguments byte-swapped
    so the varargs buffer is big-endian too, and calls through pointers by console address through each
    function's universal entry (no wasm type check, as the N64 had none). The kit's host (`host.c`, `render.c`,
    `audio.c`, with `PORT_WEB`) is compiled to WebAssembly beside it, sharing its 4 GB memory (its own data from
    0xB0000000). The host's threads are coroutines switched with JavaScript Promise Integration (`web/co.c`,
    `web/runtime.js`: Chrome and Edge 137 and later, Safari 27); a thread spinning on memory gives the page a turn
    when the next retrace is due (`port_web_deadline`), as the native host's main thread runs beside it. A browser without JavaScript
    Promise Integration plays `app-asyncify.wasm` instead: the host and the game merged into one module (Binaryen's
    `wasm-merge`; the host exports the memory it imports, under its own import name, so the game's import finds it)
    and rewritten with Asyncify (`wasm-opt`, with `port_web.suspend` as the one import that pauses), so a waiting
    thread unwinds its stack into a buffer of its own and is rewound through `co_main` (`?asyncify` in the page's
    address chooses it anywhere). Binaryen is pinned in toolchains.toml. Every data segment is above 2 GB,
    which Safari places as if negative and refuses, so the page places them itself (`placeDataOurselves`: each
    becomes passive and is copied in after instantiation). The page (the kit's `web/`) shows the
    frames, plays the sound, reads the keyboard and gamepads, and keeps the ROM and the saves in the browser. The
    racing game's frames are byte for byte the native build's (its opening, menus and a race). *For a web browser,
    faster where it can (SIMD)* is the same export carrying the host built with WebAssembly SIMD as well
    (`host-web-simd.wasm`, `app-asyncify-simd.wasm`): the renderer runs about a tenth faster, frame for frame the
    same, and the page plays those where the browser validates SIMD (Chrome 91, Firefox 89, Safari 16.4 and later),
    the plain ones elsewhere (`?nosimd` chooses them anywhere);
  - *For Miyoo Mini (SD card)*: the native build for the Miyoo Mini family (Mini, Mini Plus, v4, Flip; 32-bit ARM
    Linux), `port/wasm/miyoo.py`. The game's C is the native build's own wasm2c output; only the host is compiled
    again, by the pinned Zig for `arm-linux-gnueabihf` (glibc 2.28, Cortex-A7), linked at 0x40000000. Two kit files
    take the place of what a 32-bit computer can't do: `miyoo/mem32.c` replaces wasm2c's allocator, mapping only
    what the game touches, at its own address (low memory with page 0, the shadow stacks, the MIPS stacks of
    recompiled C and of the interpreter, the console's RAM and its registers; I/O beyond those lands after the registers by its low bits, host.c's `IO_FAR`), so a console
    address is still the host's address; `miyoo/miyoo.c` replaces `live.c` with the device's own SDL 1.2, opened
    at run time (the panel turned 180 degrees, the buttons as keys, sound through the audioserver: what the Miyoo64
    ports, mr-finster/miyoo64, found on devices). The pack is `App/<Game>/` (program, `launch.sh`, `config.json`:
    the stock OS and Onion) and `Tools/miyoomini/<Game>.pak/` (MinUI), with the project's own ROM copied in as
    `App/<Game>/rom.z64`, so the folder goes on the card as it is (a ROM put there by hand works in any byte order:
    the host turns .v64 and .n64 into .z64 as it reads them). What to copy where shows as numbered steps under the
    export's log, not in it. Under qemu-arm a racing game
    boots to its language menu with the PC build's frames; on a device it is untried;
  - *For Steam Deck (non-Steam game)*: the native build for the Steam Deck (SteamOS, 64-bit x86 Linux),
    `port/wasm/steamdeck.py`. The game's C is the native build's own wasm2c output; the PC's own host (`live.c`,
    wasm2c's 64-bit memory, `window/sdl.c`) is compiled again by the pinned Zig for `x86_64-linux-gnu` (glibc 2.28,
    the Deck's Zen 2), from whichever computer exports, with the recompiled C as the app export has it. The game
    plays in its window of its own (`PORT_WINDOW`), full screen from the start (`PORT_FULLSCREEN`), the Deck's
    controls a gamepad through Steam Input. SDL3 isn't in the pack: `launch.sh` takes `lib/libSDL3.so.0` beside it,
    the system's, else the Steam client's own copy (`~/.steam/root/ubuntu12_64/`). The pack is `<Game>/` (program,
    `launch.sh`, the project's ROM as `rom.z64`) and a README; the player adds `launch.sh` to Steam as a non-Steam
    game in Desktop Mode and plays it in Game Mode. A racing game links whole for it from a Mac; on a Deck it is
    untried;
- `PORT_LIVE=8642`: the game played in a browser (`live.c`). The host serves `http://127.0.0.1:8642/`, this
  machine only, which shows each frame and sends the keyboard or a gamepad to the first controller (arrows steer, X is A,
  Z is B, Enter is Start; a gamepad in the browser's standard layout), and can go full screen. A key held wins over `PORT_INPUT`, so a script can bring the game to a race and leave it to
  the player. With `PORT_LIVE` the game runs until it's stopped. On Windows it uses Winsock (built, not yet run there);
- `PORT_WINDOW=1`: the game in a window of its own instead (`window/sdl.c`), through SDL3 opened at run time
  (`PORT_SDL` names the library; otherwise an app's `Contents/Frameworks`, then the system's). The game's threads only
  leave the newest frame and push the sound into an SDL3 audio stream (more than a quarter of a second queued is
  dropped, so the sound never lags); the host's main thread draws and reads the keyboard and gamepads every retrace, as
  macOS lets only the main thread touch a window. Keys as the browser's, F or F11 for full screen, a gamepad in SDL's
  gamepad layout; closing the window quits. With no ROM named, the window asks for one (`port_live_rom`). Where SDL3
  can't open it falls back to `PORT_LIVE`'s browser view, or stops (exit 5) rather than run unseen. Windows has no SDL3
  here yet;
- the VI's blanking (`osViBlack`; `port_vi_black` in render.c): libultra starts the VI blanked and a game un-blanks it
  once its first frame is drawn and blanks it around a change of mode, so the leftovers of a buffer it hasn't drawn
  into yet never reach the screen. The port ignored it and showed them for a few frames at each change of screen (a
  flat green frame at the start, noise and sheared text between a 320 x 240 screen and a 640 x 480 one);
  `PORT_SWAP_LOG=file` (or `-`) lists each frame the VI was handed with the graphics task it came after;
- the RSP and RDP in software (`render.c`): each graphics task's display list is drawn into the game's own frame
  buffer, by the microcode the task loads (Fast3D, F3DEX, F3DEX2 and S2DEX by the signature in its data; Rare's
  F3DDKR, which has none, by its data's CRC), and `PORT_FRAMES=folder` saves the frames shown as PNG.
  2D screens draw correctly; the 3D path draws a racing game's races, textured.

#### Functions with no C yet

A function the game's C calls (or reaches through a pointer, or starts a thread at) that has no C yet runs from the
player's own ROM as the game plays: the host's interpreter of the console's CPU (`consoles/n64/port_wasm/mips.c`:
MIPS III with its FPU as libultra's games use it) runs that function's own instructions, from the console's memory,
where the boot and the game's own loads put them. No build holds any of the game's code, so a build made from the
save alone (a GitHub runner's, `port/wasm/workflows.py`) plays as a local one does, and the browser
build, which recompiled C hasn't reached, runs these functions too (the Miyoo Mini's, exported from the app, has the
recompiled C as the app has).

- The build's `interp.c` (`port/wasm/interp.py`) is the C between them: each such import of the game's C puts its
  arguments where MIPS o32 passes them and runs the interpreter; `port_mips_calls` holds every function that has C,
  the game's (through its exported function) and the host's (libultra's it replaces, by name), so interpreted code
  jumping to one runs that C with o32's arguments and goes on at `$ra`. Interpreted code calling interpreted code is
  only a jump. A function pointer to one goes through host.c's `port_func_as` (native) or `web/be.c`'s `port_icall`
  (`port_mips_icall`) with the call's types, so its float arguments go where o32 puts them (`$f12` and `$f14` while
  they lead, a word after anything else; `port_mips_typed_call`) and a float result comes back from `$f0`: natively
  the call's C type comes as text (bemem.py's CALL_INDIRECT) and picks an entry of that type (interp.c's
  `port_mips_pointer_types`, one per type the game calls through pointers as); in the browser the call's count
  carries them (webir.py's `_type_code`, two bits an argument).
- What WebAssembly passes differently: a variadic function's extra arguments (a pointer to a buffer of them, from
  the words o32 gives them, big-endian in both builds: `port_mips_varargs` and `port_mips_spread`; only a
  function's own definition, or a declaration with fixed parameters before its `...`, makes it variadic: an
  unprototyped `s32 taskOpen();` is `(...)` in the IR too), and a structure passed by value (a pointer to its words
  where o32 put them, `port_mips_slot`, the arguments after it in the words after all of it: its size from the IR's
  type definitions; the game's C passing one to a function the interpreter runs: its words copied in, sized from its
  callers' declarations, `port_mips_copy_in`). A racing game's allocator takes its block size as an extra argument: passed the next register
  word as the buffer, it read its size from address 8 and never returned. The functions the interpreter runs get
  MIPS's own shape in the build's one signature per function, so callers that declare one differently still link
  (as recompiled C's do).
- A call through a pointer is a safe point (another thread may take over), as for the game's C; a direct call never
  is: one was, and a thread the C had just started ran before the function starting it was done.
- Not interpreted, as before: what the build leaves out on purpose (exclude.txt, the library code the host provides)
  and the library code the port kit says the host replaces (libultra's hardware code: the Controller Pak's serial
  transfers, interrupts). Those stay the logging stubs they were.
- Each host thread interprets on a MIPS stack of its own in the console's address space (from 0x14800000, mapped on
  the Miyoo Mini too), below whatever frames it already has. A thread spinning in interpreted code gives the others
  their turn every 65,536 instructions.
- `PORT_INTERP=0` turns it off; `PORT_INTERP_TRACE=1` says each function it starts, `2` each call into C; the
  browser build takes either in its address (`?PORT_INTERP_TRACE=1`).

Tried on a racing game with the C of 300 of its game functions taken out of a copy of its save (88 of them run, a
quarter of a million calls in a minute): the native build plays the same scripted minute as the full build (its
boot, menus and attract races; which race the attract shows is random), and the browser build boots, goes through
the menus and races. Each bug found on the way (a variadic allocator, an unprototyped function taken for a variadic
one, a structure by value before other arguments, safe points at direct calls) was found by taking out half of the
functions until one alone broke the game. `tests/test_n64_port_interp.py` runs small MIPS programs and checks the
generated bridges.

#### The browser build's renderers

The browser build's page offers two renderers (its **Renderer** choice, kept in the browser; `?renderer=webgl` or
`?renderer=software` chooses for one visit), and the player can switch while the game plays:

- **Software** (the default): `render.c` as above, every pixel as the console draws it, the frame read from RDRAM
  and filtered as the VI shows it.
- **WebGL**: the display lists are read, transformed, lit and clipped by `render.c` as for software, then each
  primitive goes to the page with the RDP's state (`render_gpu.h`) and `web/gl.js` draws it with WebGL 2, at 1x to
  4x the console's resolution (**Resolution**). Textures are decoded from TMEM in C (as the software sampler reads
  them), each sent once by the hash of what made it; the shader per setting does the combiner, the blender (its
  frame-buffer reads as GL blending), alpha compare, the samplers' wrap, mirror, clamp and filters (the RDP's
  three-texel bilinear), levels of detail and the fog the RSP gives; depth is a depth buffer per z image, cleared
  when the game fills it. Each frame buffer the game draws to is a texture of its own, shown when the VI is given
  its address.
- **Smooth motion** (WebGL's in-between frames, on by default): the game's motion drawn on every refresh of the
  screen, not only on the frames the game draws (25 a second for a PAL game, 30 or 20 for most NTSC ones), at the
  game's own speed, one game frame behind. Each triangle's vertices go in clip space twice (`render_gpu.h`,
  `gpu_match`): as this frame has them and as the previous frame had the same vertex, found again by what the game
  loaded for it (its model-space position and texture coordinates) and how many such came before it in the frame,
  so a model is found again wherever the game put its vertices and matrices that frame. One moved further than a
  camera cut's worth (half the screen) starts where it is. The page records each game frame's draws (`gl.js`,
  `Frame`) and draws it again on every refresh, its vertices `t` of the way from the previous frame's places to its
  own, each time from a copy of its frame buffers as they were before it, so what it blends isn't blended twice.
  Rectangles, 2D and lines aren't moved. In a racing game's races about three in four of the vertices drawn carried a
  previous place, and what didn't was mostly roadside clutter coming into view (half of the vertices it loads are
  found again; `?verbose=1` says how many every 250 frames); a vertex not found is drawn where its frame has it.

What the WebGL renderer doesn't do yet, which software does (ROADMAP.md, "The browser build's WebGL renderer"):
- frame-buffer effects: the GPU's frames aren't in RDRAM, so a game that reads its frame buffer back (a frame as a
  texture, a pause screen's copy, a motion blur) reads what the CPU last wrote there. A frame the CPU writes itself
  (a logo, a movie) is still shown: the GPU's frame is shown only while the frame buffer's RDRAM is as it was when
  the GPU last drew it, otherwise RDRAM's, as software shows it;
- the RDP's coverage anti-aliasing and dither, and the VI's filters other than gamma (anti-aliasing, dedither,
  divot); 8-bit and 32-bit frame buffers are drawn as colour like the 16-bit ones;
- a blender whose first cycle reads the frame buffer (rare) takes the pixel itself in its place.
- in-between frames: a game that makes a model's vertices anew each frame (animated on the CPU, or in camera space)
  shows that model at its game frames while the rest moves smoothly; a camera cut within half the screen sweeps
  for one game frame.

A racing game's boot, menus (2D backgrounds, sprites, a 3D car), attract races and a race were checked against
software in Chrome, switching between the two on the same frames, and with smooth motion: drawn at a frame's start,
middle and end its frames differ, and the same moment drawn twice is the same to the pixel. `tests/test_n64_port_render_gpu.py` runs display
lists through the GPU path and checks what the page is sent; `tests/web_gl_shaders.mjs` the shaders' combiner and
blender.

On macOS the host is built by the system's clang, because Zig 0.14.1 can't link against macOS 27's system library.

Found on the first game, all things the judge can't see:
- a `switch` whose `case` labels disagreed with its jump table, and still matched;
- C that reached locals through the console's stack layout;
- functions the split had left as data.

### 5. Play, and check that it runs

A **Play port** button builds and starts it. A headless check runs N frames: it starts, doesn't crash, and the
screen isn't one colour. To compare its frames with the original's, `tools/n64_frame_compare.py` runs both with one
input script (timed in retraces on both sides: `PORT_INPUT_CLOCK=vi`), writes each one's RAM at chosen retraces (the
port's `PORT_RAM_DUMPS=vi:file,...`, the emulator's dumps), and draws the graphics tasks it finds there with the
port's renderer, side by side with their difference:

```bash
uv run python tools/n64_frame_compare.py baserom.z64 --port port/wasm/build/game --vis 1750 --input @menus.txt --out report
```

Both frames come from the same renderer, so a difference is the game running differently, not the drawing.
`tools/n64_vi_compare.py ROM --port BUILD --input @script` checks the VI's blanking against the console's: the
emulator's video plugin logs the VI's registers (`EMU_VI_LOG`; osViBlack blanks by writing 0 to the horizontal video
register, the status stays), the port's `PORT_SWAP_LOG` says when it blanked, and the two lists of blanks must agree in
number and length. On a racing game they did: six blanks of 25, 25, 21, 25, 25 and 21 graphics tasks on both sides
(where each began differed for three of them: a menu advanced hundreds of tasks apart between runs). On the console the
screen is black at those moments; before the port honoured osViBlack it showed the buffer's leftovers instead.
`--tolerance PIXELS` on `tools/n64_frame_compare.py` also reports the share of pixels with no equal pixel within that many
pixels of the other side's picture, which separates a scene drawn the same but a little apart from one drawn
differently. On a racing game: the paused starting grid and the first frames of the race, 29 to 30 percent of pixels
differing exactly, had 0.06 to 0.19 percent with no match within 1 to 3 pixels (the same picture, a pixel or so apart);
a later moving scene stayed at 31 percent within 3 pixels and 11 percent within 16: the camera and the cars were
somewhere else on the track, game state that had drifted, not a drawing difference.
`tools/n64_task_diff.py CONSOLE_DUMP PORT_DUMP` says where two pictures of one task differ: the task's display list
is traced on each side and compared with its pointers masked, so what's left is values the game computed. A racing
game paused on its starting grid (the pad released, one START: the scene stands still on both sides, whatever the timing)
ran 168 commands on each side and only one differed, the horizon's fill rectangle by one pixel. The emulator's `--break`
on the function that wrote it (`drawPanelFill`) showed the console's own horizon settling from 146 to 140 over about 200
retraces at the race start, then holding while paused: the port was paused nine retraces later in that settling, so
the difference was when START landed, not a drawing or logic fault. A moving scene can't be matched by task or retrace
number, but a scene held still by a pause or a menu can, and a difference there is found to the command.
The functions a game's `.original` file names run from the original instructions: N64Recomp translates them where the
build has it (Play), and the interpreter runs them everywhere else. Until the interpreter's own list of what it leaves
alone stopped including them, a build with no recompiler (the command-line build, and the browser export, which has
none) stubbed them to nothing while saying the interpreter ran them: a racing game's wheel rotation and its menu's quad
allocator did nothing in a browser export (`applyPackedRotation: not implemented, skipped` in the page's console).
`PORT_VTIME=1` makes the native host's time virtual: the retrace fires when every game thread is waiting (or only the
lowest-priority thread, the console's idle spin, is left) and the clock the game reads (osGetTime, the count register,
the audio interface's drain) advances a retrace at a time, a microsecond per read within one. A run is the same
whatever the machine's load (two runs of one retrace-timed input script had the same RAM at retrace 600 and 1,500, the
same graphics tasks, swaps and DMAs) and takes less than the real time (2,000 retraces in 29 seconds). It showed what
the console and the port have apart: counted in retraces, a racing game's video-mode blanks last 42, 27, 12 and 76 on the
console and about 10 in the port, though they are the same 25, 25 and 21 graphics tasks on both: the console spends two
to seven retraces on a frame where the port, with no CPU time to spend, spends one, so a scene is the same by game frame
and not by retrace. `EMU_VI_LOG` lines now carry the retrace (`vi V`) as well as the task.
What `PORT_VTIME` did and didn't settle for a racing game, with the same retrace-timed input on both sides: the port's
video-mode blanks fell at the console's graphics tasks within one frame (485, 521, 632 against 485, 521, 637; the race
start at 2,727 and 2,773 exactly), where the wall-clock port had been hundreds of tasks apart, so the two run the same
screens in the same order. The retraces between those events still differ (task 2,773 to 3,194 took 216 retraces on the
console and 169 in the port), because the console adapts its frame rate to how long its frames take (2.57 retraces a
frame there, the port 2). Charging each graphics task a fixed virtual time (tried, 3 to 6 ms) doesn't reproduce that: below
about 3.8 ms nothing changes, above 4 ms the game drops to one retrace a frame, so a cost per task was not kept.
Pausing the port at as many retraces after the race start as the console had brought a paused race grid from 29 percent
of pixels differing exactly to 3.3 percent with no match within 3 pixels, 167 of its 168 display-list commands equal
(the one that differs is the horizon's fill rectangle, whose value follows the car's pitch as it settles and moves from
133 to 138 to 117 as the pause moves by 30 retraces); an exact match there needs the car's physics to reach the same
state at the same retrace, which the port's pacing doesn't yet give.
`tools/n64_frame_scan.py FOLDER` needs no emulator: it looks through the frames a port saved (`PORT_FRAMES=FOLDER
PORT_FRAME_EVERY=1`, `PORT_SWAP_LOG=file` to name each frame's graphics task) for static, flat-colour frames and runs of
black, the ways a buffer shown before the game drew it looks. Moving scenes can't be compared pixel for pixel by task
number: the runs drift (the console drops frames, the port doesn't), so a race differs by a few pixels of scroll even
at the same task; menus, the title and scripted still scenes match to the pixel.
Each frame dump has a `NAME.task` beside it, the 64-byte task the RSP was given (the emulator's from DMEM, the port's from
its RAM): a game keeps its own copy of the task wherever it likes, so scanning RAM for one finds nothing in some games,
and something that only looks like a task in others. The tool draws that task, and pairs the two sides' pictures when each
drew one at a different frame-buffer address. `--frames` takes runs (`4000-4011`), as only some of a game's tasks (about
one in five in a racing game) draw the picture. A menu and a first 3D scene of a racing game matched to the pixel.
`--frames` compares the game's own frames instead of retraces: the nth graphics task it hands over, the RAM written
as it is handed over (the emulator's video plugin, `EMU_FRAME_DUMP`; the port's `PORT_FRAME_DUMPS`) and the input
timed the same way (`EMU_INPUT_CLOCK` / `PORT_INPUT_CLOCK=frames`). Neither clock makes the runs one: the console
drops frames and spends retraces loading, the port doesn't, so a game timed by retraces is elsewhere at the same
frame, and further on at the same retrace. `--port-vis` pairs each of the console's retraces with the port's at the
same moment, `--port-input` gives the port its own script; what moves with time (scrolling clouds, a pulsing
highlight) still differs, and menus where the game waits compare best.

When the port behaves differently from the console, the N64 reversing emulator (mupenRE, under Compilers & tools →
Tools; `consoles/n64/emulator`) runs the user's own ROM headless with the same input script, timed in vertical
interrupts (a game drawing 30 frames a second takes two VIs a frame). Its RDRAM dumps have the port's layout, so
`emulator.differences` compares the two word by word. Its breakpoints show which code writes an address, or what a
function is called with:

```bash
python -m decompnet.consoles.n64.emulator baserom.z64 --vis 2600 --input @race.txt --watch 802A45C8 --from 2400
```

That is how a racing game's backwards start was found. Each car's matrix came from an Euler-angle helper; the
console passed it a yaw of 0x800, and the port passed 0. The yaw was the `$v0` a void function left for its
caller, and the port had overwritten it (`port/wasm/adapt.py`).

The same game could line up correctly and still spin during a race or the title-screen demo. Library detection
renamed two conversion routines that its arctangent called, then the build lost their ROM addresses and stubbed
both calls to zero. The build carries those addresses through the rename so both CPU fallbacks can run the routines.

The recorded RDP commands also explained the black stippling behind the race HUD. The graphics microcode
starts each task with alpha dither disabled. When the HUD changed only the alpha compare setting, the software
renderer carried dither over from the preceding task and drew some fully transparent pixels. The renderer now
starts each F3DEX2 and S2DEX2 task with the microcode's defaults for partial updates. This also fixes the image
RT64 presents when the VI needs pixels from the software framebuffer.

The build also lists what to look at before playing. Matched C is judged by one compiler, and on MIPS a call works
whether or not its C agrees with the function it calls. So matched C can take a result from a function it declares
void, call a variadic function through a fixed prototype, or pass a `u8` where the callee takes an `s32`. Each one is
right on the console and can behave differently in any other build. The WebAssembly build bridges each one as MIPS
behaved (`port/wasm/adapt.py`) and records it (`port/wasm/calls.py`: `build/calls.json`). Port readiness then lists
them by kind, worst first, under **Calls agree with what they call**. On a racing game, 794 calls in 505 functions;
the 19 worst take a result from a void function, and the backwards start was one of them. Putting their declarations
right while the function still matches makes the C say what the console does.

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


## Port compiler compatibility and recompiler failures

Recovered USO and UV analysis banks use bank-relative offsets, and uncompressed USO text can have provisional
analysis addresses before the loader relocates it. Recompile and the compatibility build leave their functions and
saved C out until their runtime load addresses and relocations can be associated with them. The interpreter remains
available for code the game loads into memory.

The WebAssembly compiler can reject a redundant scalar `extern int g;` when the shared header already declares
`g` as `volatile int` (including a typedef of it). The generated build may retry without that redundant line only
when clang reports the same canonical scalar type with additional shared `const`/`volatile` qualifiers. It keeps
the stronger shared declaration. Pointer/array type changes, narrower types, conditionals and source-only
qualifiers are not removed. Saved and judged C stays unchanged. When saved game C still fails to compile and its
function has a usable fixed console address, the compatibility build can leave that file out and run the original
function through N64Recomp or the interpreter. It reports those files in the build log. A port override, library
source, or a file whose other required helpers cannot be resolved still stops with its compiler errors.

A native N64Recomp crash is reported with its signal. Before setting a body aside, the app checks that disabling
all bodies avoids the crash, narrows the affected subset and confirms the individual body reproduces a native
crash. Normal refusal messages remain recorded too. The final output is a fresh translation of every body not
refused. Crashing bodies remain explicit runtime stubs, not recovered C or matches. Resource kills and global or
unresolved interaction crashes still fail with a reason. Normal refusals are processed for the whole game in one run. Cancellation and per-body/whole-game probe limits apply.
Generated static-body failures that the CLI cannot name in its patches are isolated to a patchable input body,
with that relationship retained in the refusal reason.

A compiler error inside a generated, patchable input body also leaves an explicit refusal and reruns translation;
header errors and errors outside a body do not implicate an arbitrary function.

The IR placement step respects function declarations even when a call target has a data-style imported name.
Repeated helper definitions kept as saved compilation context receive identities private to their own input
module. Each caller retains its own helper body; a real owner file retains the public function. Address-taken
uses keep the original console address, and private context is not promoted as a public runtime implementation.
This avoids duplicate symbols without selecting an arbitrary helper variant or relaxing the linker.
