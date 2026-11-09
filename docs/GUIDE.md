# The decompdecomp guide

This guide tells how decompdecomp operates. For the installation, read the [README](../README.md).

- [How it works](#how-it-works)
- [Public decomps](#public-decomps)
- [Features](#features)
- [Agents](#agents)
- [Decompile together](#decompile-together)
- [Platforms](#platforms)
- [The export](#the-export)
- [Installation details](#installation-details)
- [Your work](#your-work)
- [Problems](#problems)
- [Development](#development)
- [Built on](#built-on)

## How it works

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../.github/assets/how-dark.png">
    <img alt="Your ROM, split into every function by splat. Your agent writes C, the original compiler builds it, it's linked at its real address and compared byte for byte. If it doesn't match yet, the diff goes back to the agent." src="../.github/assets/how-light.png">
  </picture>
</p>

1. **Open a game.** decompdecomp finds all the functions and data, and it identifies the compiler of the game.
2. **Start helper agents.** Each agent claims a different function. It gets the assembly, the data, the callers, a first
   draft from a machine and the notes from earlier attempts.
3. **Look at the matches.** decompdecomp compiles each attempt, *links* it at the real address of the function and
   compares it with the game. If it is not a match, the agent gets an instruction diff. If it is a match,
   decompdecomp keeps it and shares it with all agents.

Only an identical result (score 0) is a match. decompdecomp links each attempt at its real address. Thus it also
checks calls, globals, strings and jump tables.

<details>
<summary><b>An example match</b></summary>
<br>

The assembly from the ROM:

```asm
800007D0  slt     $v0, $a0, $a1
800007D4  bnez    $v0, .L800007EC
800007D8   addu   $v0, $a1, $zero
800007DC  addu    $v0, $a2, $zero
800007E0  slt     $v1, $v0, $a0
800007E4  beql    $v1, $zero, .L800007EC
800007E8   addu   $v0, $a0, $zero
.L800007EC:
800007EC  jr      $ra
800007F0   nop
```

The C from an agent. KMC GCC 2.7.2 at `-O2` compiles it to the same bytes:

```c
s32 clamp_s32(s32 v, s32 lo, s32 hi) {
    if (v < lo) {
        return lo;
    }
    if (v > hi) {
        return hi;
    }
    return v;
}
```

</details>

## Public decomps

Many games have a community decomp project. Each project has a build and some decompiled code. decompdecomp uses
these projects as they are:

1. **Fork a project.** Click **New project → Continue a community decomp**, and paste the address of a decomp
   repository. decompdecomp shows what the project says about AI, then clones it to your computer, on a local
   `decomp-wtf` branch.
2. **Add your copy of the game.** The app knows the usual project layouts (dtk-template, configure.py and ninja,
   splat and make). It puts your copy in the correct location and runs the setup of the project. For other layouts,
   follow the README of the project. For an N64 splat project that expects a `.z64` ROM, you can supply the same
   ROM in `.v64` or `.n64` byte order; decompdecomp saves a `.z64` copy for the project's build. When the project
   declares a SHA-1, decompdecomp checks it against the normalized ROM before copying.
3. **Start helper agents.** Each agent writes code in the real source file. The build of the project compiles it, and
   [objdiff](https://github.com/encounter/objdiff) compares it.
4. **Examine the commits.** Each match is a git commit on `decomp-wtf`. decompdecomp sends nothing upstream.

**Limits on agents.** Agents can change only C and C++ source files and headers. They cannot change targets,
assembly or build scripts. decompdecomp refuses inline assembly. If a change breaks a function that matched before,
decompdecomp reverses the change.

**Linked files (GameCube and Wii).** A file is *linked* when all its functions and data match. Then the project
builds the game from the C of that file, and a checksum examines the full game. A port needs linked files.
decompdecomp helps with these steps:

- It gives each function a source file. Many projects have code that is in no file.
- It shows the agent the data of a file that is almost complete, next to the original data.
- It links a complete file automatically. It keeps the change only if the checksum of the game stays correct.

Imported projects with verified assembly references can also use Autopilot: it first probes C candidate
files with the project's own compiler. It prepares up to 32 new units per run; **Run again** prepares the next
batch. The original assembly remains the linked baseline until the project integrates the recovered C.
An unsupported C build rule still needs project-specific setup.

## Features

| Feature | Description |
| :-- | :-- |
| **Live view** | A map of all the functions in the game. You see the tool calls and the scores of each agent in real time. |
| **Autopilot** | Matches without cost. Code that occurs more than one time gets the C of its matched twin. Then machine drafts go to the judge. (Mario Party 6: 762 functions in 12 minutes.) |
| **Permuter** | Rewrites near misses (at most 6% of the function differs) thousands of ways in the background, with decomp-permuter (install it in Compilers & tools). It uses spare cores at a low priority. The judge decides what matches. Without decomp-permuter, a few simple rewrites run instead. |
| **Batches** | Agents write many small functions in one turn. decompdecomp judges them in one call. |
| **Editor** | Each function has an editor. Write C, push Ctrl+Enter and look at the diff. |
| **Suggestions** | Agents can suggest a different compiler, for the full game or for address ranges. decompdecomp tests each suggestion first. You apply it with one click, or let proven suggestions apply automatically (a setting of each game). |
| **Test installed compilers** | In Settings → Compiler, compare up to 400 existing unmatched C attempts with this console’s installed compilers. Runs locally without AI tokens or downloads. Reports exact alternative matches without changing settings, including when automatic changes are enabled. Reports every matching profile, including the current settings. Function comparisons show all tested profiles ranked by percentage; project comparisons list improvements ranked by resulting percentage, with an apply button per function. Applying revalidates the saved C and changes only that function’s compiler range. Project-wide and single-function tests use the same supported flag-search and debug variants; project-wide runs therefore take longer. No result is not proof that a compiler is wrong. An unmatched function also has **Test installed compilers & flags**, which tries its saved C with the supported optimization, small-data and other flag-search variants plus debug variants. This is not an exhaustive search of all possible flag combinations. ROM-mode MIPS projects for now. |
| **Flag search** | `search_flags` builds a function again with each flag of its compiler changed, one at a time (up to 12 builds), and says which change gets closer. MIPS for now. |
| **Shared header** | Structs and prototypes go into one header. decompdecomp accepts a change only if all matches stay matches. |
| **Safe names** | Matches use addresses. A new name for a function or data cannot break a match. |
| **Look-alikes** | `find_similar` compares instruction sequences across the full game and finds copied and inlined code. |
| **Compiler map** | Big games mix compilers. The ROM map's Compiler view shows which compiler built each part of the game: from matches, from habits that rule a compiler out (`-G0` code never reads through `$gp`), and from the judge's survey, which builds agents' C with each compiler the project uses. A part the judge confirms becomes a suggestion. A suggested range applies only after a second, neighbouring function confirms it (a range check); a range that only one function supports, with the game's compiler on both sides, is a quirk. decompdecomp checks the ranges a project already has again by itself. Agents read it with `compiler_map`. MIPS for now. |
| **Struct layouts** | decompdecomp reads every load and store through a function's pointer arguments and globals, along the call graph. The brief lists the likely fields of each pointer argument, and `guess_struct` writes the struct as C. MIPS (N64, PlayStation, PlayStation 2) for now. |
| **Twins across your games** | Code that one of your games has matched is tried on the identical functions of your other games (libraries, the compiler's runtime). No agent time. Imported decomps can also donate verified C to ROM projects: identical machine-code fingerprints select candidates, and the receiving ROM’s compiler and byte-for-byte judge must verify every match. Functions with missing types or macros remain unmatched. |
| **Similar code across your games** | After exact transfers, autopilot tries close verified C and small constant edits in MIPS ROM projects. No tokens; only byte-identical results count. Attempts are bounded and failed candidates are cached. Run again to continue. |
| **Shared knowledge** | Agents propose what they learn about a game. A proposal counts when helpers of two different models back it. Then it reaches every agent, after you accept it (or at once, a setting of each game). Run helpers of several models for this. |
| **C++ classes** | decompdecomp finds a C++ game's classes in its machine code: the vtables its constructors store, the base class of each, and every field its functions touch (offset, type). Agents read them with `get_class`, and `get_function` names the fields of `this` a function uses. MIPS (N64, PlayStation, PlayStation 2) for now. |
| **Drafts** | m2c for MIPS, PowerPC and ARM. Ghidra (optional) for x86, AArch64 and other architectures. |
| **Guides** | Agents get guides for each architecture and compiler, and for C++ and objdiff. |
| **Disc formats** | ROMs, PlayStation, PlayStation 2 and PSP discs (ISO, BIN, CHD, CSO), GameCube and Wii discs (ISO, GCM, RVZ, WIA, WBFS, CISO, GCZ) and program files. |

### Reusing similar functions without AI

Run autopilot after another compatible ROM project has verified C. It learns names and conservative byte-offset
changes from accepted transfers, tries close C, and gives close failures a bounded search guided by the assembly
differences. Tiny-function templates also work without another matched project. Every result still passes the
ordinary compiler/link byte check.

New matches automatically queue compatible projects. In **Settings**, turn automatic reuse on/off and set the
elapsed-time and attempt budgets (two minutes / 128 by default, shared across an automatic wave). **Stop** pauses
that project's automatic reuse; **Run again** resumes it. Completed trials are cached across restarts. Changed
headers, symbols, compiler settings and source make affected trials eligible again.

For library code, open the SDK panel and supply a local object archive, its optional C file/folder and an optional
self-contained declarations header. Fingerprints select definitions to try with the compiler used when importing.
This does not download source, run SDK build scripts, or resolve a library's header dependencies for you.

A **compiler** result means the candidate matches with the donor's flags. Its C and settings are saved in the
function's notes for review. It is not counted as progress until accepted with the project's build settings.
These additional searches currently support MIPS ROM projects; imported repository projects retain exact transfers.

## Agents

| Agent | Connection |
| :-- | :-- |
| **API key** (recommended) | The main helper. The app talks to the model's API itself, with your key: nothing else to install, and no other program's update can break it. Each OpenAI-compatible API works: DeepSeek, OpenRouter, OpenAI, or a local model with llama.cpp, Ollama or LM Studio. |
| **Claude Code** | Operates without a window, through the local MCP server of the app. |
| **Codex CLI** | The same. It can approve only the tools of decompdecomp. |
| **Antigravity CLI** | The same, through the MCP configuration of the workspace (`agy`). |
| **Kimi Code** | Install `npm install -g @moonshot-ai/kimi-code` (Node.js 22.19+), then `kimi login`. Uses your Kimi Code account and configured models. The legacy Python `kimi-cli` is not supported. |
| **opencode** | The same, with each provider that you set up: DeepSeek, Qwen, Kimi and more. |
| **Cursor Agent** | Runs headlessly with the app’s MCP tools in a private configuration. |
| **Kimi Code** | Uses the npm CLI with a private configuration and app MCP tools. |
| **ZCode CLI** | The same, with Z.ai's GLM models. It reads the prompt from a file it attaches. |
| **Oh My Pi** | The same, with each provider that you set up: Anthropic, OpenAI, z.ai, Mistral and more. |

The app starts each helper itself, from **Start agents** in the project's header. A helper that stops starts again
by itself. On a usage limit it waits until the limit resets. Five fast failures in a row pause it, and the app says
why. Run several helpers, of different models: they work on different functions at the same time. Choose **Multiple
models** in the launcher to pick several at once; the app remembers the choice. One start can begin at most 24
helpers in total.

**Paused candidates are model-specific.** An unchanged function pauses after three releases or
reported exhaustion for that recorded model. A different model can still attempt it. Use the worker's
**⋮ → Retry paused functions**, or the button on a waiting card, to grant one fresh turn to up to five
functions by default (maximum 50). Existing work filters, active claims and compile limits remain.
Grants are consumed when claimed and persist across restart. Waiting workers check within 30 seconds;
stopped workers stay stopped. Running workers may resume normal paid model calls. See the README's
work-selection section for model identity and legacy-pause behavior.

Agents get only the tools of decompdecomp. They get no shell. They cannot access your files, except the source tree
of a fork. The app installs missing agents and tells you how to sign in.

You pay your provider. In our tests, one small function cost approximately 8 cents with Claude Code. Large functions
need many more attempts. Less expensive models often fail on register allocation in large functions.

**Local models.** Start the model with a minimum context of 16K tokens:

- llama.cpp: `-c 16384`
- Ollama: `OLLAMA_CONTEXT_LENGTH=16384`. If you do not set it, Ollama removes the start of each request.
- LM Studio: Context Length

Then select the preset in **Agents & models → API key**. If the model does not use llama.cpp, set the context size
there too. Small models can do small functions. Large functions need a strong model.

## Agents outside the app

A script or another tool on this computer can work one project through the app's tools, once you approve it. It asks
on its own (its name and a secret it makes up), and the Agents & models page shows the request: pick the project it
may work and approve it, or refuse. The agent reads the answer — its token — with its secret, and from then on its
tools are that project's and nothing else. Refused or idle requests show up only while they wait.

```sh
curl -s http://127.0.0.1:6464/api/pair/request -d '{"name":"my-script","secret":"a-long-secret-only-i-know"}'
curl -s http://127.0.0.1:6464/api/pair/status  -d '{"id":"…","secret":"a-long-secret-only-i-know"}'
```

The port is the app's own (shown in its window title and `~/.decomp/server.json`). The app never sends a token
anywhere: it waits for the agent to come back with the secret only it holds.

## Decompile together

A team of any size can work on one game. Each person uses their own computer, their own copy of the game and their
own agents. One computer is the **host** of the room. The host:

- gives out functions, so that two people do not do the same function;
- checks each match against its copy of the game;
- shares names, structs and notes with the team.

No data goes through a website of decompdecomp. The team hosts its room and controls who can join.

**Host a room.** Open the project and click **Decompile together** in its header. Choose who can join and how
teammates reach your computer, then click **Start sharing**. Send the link.

**Join a room.** Click **New project → Join a team's room**. Paste the link, type your name and pick your own copy
of the game.

| | |
| :-- | :-- |
| **Access** | A private room needs the invite link. A public room needs only the address. You can approve each new member. |
| **Address** | A free Cloudflare quick tunnel, or your own address: LAN, Tailscale or a server. A room on your own address keeps its link after a restart. |
| **Host controls** | See who is online, let people in, remove members, make a new invite link, stop sharing. |
| **Load on the host** | Members compile their own attempts. The host compiles only to verify matches. |
| **Projects** | ROM projects, GameCube, Wii and PC projects from scratch, and forks of public decomps. |

No ROM data or assembly goes between computers. Each person reads the code from their own copy. All copies must be
identical. The decompiled code of a commercial game belongs to its rights holder. Share it only with people who own
the game.

## Platforms

| Platform | Status | From scratch | Compilers | Rebuildable project | Public decomps |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **Nintendo 64** | ★ **Recommended** | ✓ ROM | KMC GCC 2.7.2 · IDO 5.3 / 7.1 · SN64 GCC 2.7.2 / 2.8.1 | ✓ export | ✓ |
| **PlayStation** | Beta | ✓ disc (BIN, ISO, CHD) or PS-X EXE | GCC 2.5.7 · 2.6.0 · 2.6.3 · 2.7.2 PSX/CDK · 2.8.0 · 2.8.1 · EGCS 2.91.66 · 2.95.2, with maspsx | ✓ export | ✓ |
| **PlayStation 2** | Beta | ✓ disc or ELF | EE-GCC 2.9 · 2.95.2/2.95.3 (SN) · 2.96 · 3.2 · CodeWarrior 2.3.3 / 3.0.1 | ✓ export | ✓ |
| **PS3** | Beta | ✓ program (decrypted EBOOT.ELF; a SELF is refused) | ps3dev GCC (free) · your own SDK's ppu-lv2-gcc and SNC | — | |
| **PSP** | Beta | ✓ UMD image (ISO, CSO, CHD) with its other modules, or ELF/PRX, unencrypted or decrypted | CodeWarrior for PSP 3.0.1 · PSP SDK GCC 3.3.3 · SN pspsnc | ✓ export | ✓ |
| **GameCube · Wii** | Beta | ✓ disc (ISO, RVZ, WBFS…) or DOL | CodeWarrior GC 1.0 – Wii 1.7 | ✓ dtk-template | ✓ |
| **Windows PC** | Beta | ✓ program (32-bit x86 or 64-bit x64 .exe) | Visual C++ 6.0 · 7.0 · 7.1 · 2005 · 2008 · 2010 · 2017 (wine; 64-bit games too, with its 64-bit compiler); any other, from your own copy (Setup), for 32-bit games | — | ✓ |
| **Nintendo DS** | Beta | ✓ ROM (.nds) | CodeWarrior for DS 1.2 – DSi 1.6 (24 versions), one per source file | ✓ ds-decomp | ✓ |
| **Game Boy Advance** | Beta | ✓ ROM (.gba) | agbcc | ✓ luvdis export | ✓ |
| **Xbox** | Beta | ✓ default.xbe, .xiso or Redump .iso (as a Windows program; Ghidra finds the functions) | Visual C++ 7.0 · 7.1 (the XDK's); your own XDK's for early games, and to set its library code aside | ✓ (`ninja rebuild` writes your default.xbe back) | |
| **3DS · Switch · Xbox 360** | Public decomps only | | | | ✓ |

[ROADMAP.md](../ROADMAP.md) has a table of which feature works on which platform.

The tests below were made before 1.0.3, which rewrote most of the app: every platform needs testing again. Try
it on a game you own and say how it went; that's how a platform goes from beta to recommended.

**Recommended: Nintendo 64.** It is tested with Super Mario 64, GoldenEye and Perfect Dark, from the import to a
game rebuilt byte for byte. This includes code that the game loads later and compressed code. The export puts each
function at its original address. This is correct for matches and ports. Mods that make code larger need a
different build, which is planned.

**Beta:**

- **PlayStation** passes every step on the homemade demo game, built the way PSY-Q games were (GCC, then maspsx
  for Sony's assembler ASPSX). Each compiler uses its corresponding assembler profile. GCC 2.5.7, EGCS
  2.91.66 and GCC 2.95.2 currently need Linux or WSL; the other listed PS1 compilers have Apple Silicon builds.
  No real game is tested from start to end yet.
- **PlayStation 2** is tested on real games (GTA San Andreas, Ridge Racer V), with several compilers per game. No
  real game is rebuilt whole yet.
- **GameCube and Wii** projects are dtk-template projects, as most community decomps are. Each build makes the game
  again and compares it byte for byte. The homemade demo game passes every step, but no real game is tested from
  start to end yet.
- **Windows PC** passes every step on the homemade demo game. It is tested on few real games. Its import says what a
  program is from its headers: the Visual C++ version and build that made it, whether it's 64-bit, how much of it was
  built with whole-program optimisation (/GL), and the DRM or packer it's wrapped in. Games built with Visual C++ 6.0 to
  2010 and 2017 can match, each with the build of its compiler the program names, or the closest the packs have (2017's
  15.9 for any 2017 update; 2005 to 2010, and 2017, come in packs of their own). A game built with a Visual C++
  decompdecomp doesn't have yet builds with your own copy of it, when you set one up in Setup, else with the nearest
  decompdecomp has, and the import says so: 2015's with 2017's (much of its code is the same), 2019 and newer with
  2017's (what their compilers generate differently can't match until they come), 2012 and 2013 with 2010's (little
  matches). 64-bit games start too, and build with Visual C++ 2017's 64-bit compiler, the only 64-bit one decompdecomp
  has (a game built with another says so; your own copies build 32-bit games): their functions come from the program's
  own table (.pdata), not a guess. Programs for other processors (ARM), mostly-/GL ones and protected ones aren't
  started, and the import says why. Visual C++ 2017 (32- and 64-bit) runs through wine on Linux and macOS, which you
  install. decompdecomp never removes or gets around DRM. A game that keeps code in DLLs of its own comes with them when
  you give its path (the game's folder, or its .exe; a dropped file comes alone): each DLL is a program of the project,
  with its source files in `src/auto/<its name>/`, its exports' names and its own Visual C++. The C runtime, DirectX
  wrappers, middleware (Miles, Bink, FMOD...), copy protection, ASI plugins and DLLs built long after the game (mods)
  are left out, and the import's log says which and why. [VISUAL-CPP.md](VISUAL-CPP.md) lists the Visual C++ builds
  decompdecomp has, how it picks one, and where their code changes.

Please [report problems](https://github.com/n-delic/decompdecomp/issues). The status of a public decomp is the status of its own project.

**Tools for a start from scratch:**

| Platform | Tool |
| :-- | :-- |
| N64, PS1, PS2, PSP | splat and spimdisasm. They also find code that the game loads later, and overlays. |
| GameCube, Wii | decomp-toolkit, in a [dtk-template](https://github.com/encounter/dtk-template) project (`consoles/gc_wii/scratch.py`) |
| Windows PC | [Ghidra](https://github.com/NationalSecurityAgency/ghidra). decompdecomp cuts each function out of the .exe and the game's own DLLs into a project for Visual C++ and objdiff (`consoles/pc/scratch.py`). |

**C and C++.** GameCube, Wii, PS2 and Windows PC games are often C++. On these platforms, agents can write
classes and methods, and decompdecomp changes the C++ names back to the names of the game. N64 games use C. Public decomps use the language of their project.

**Proprietary compilers** (IDO, CodeWarrior, Visual C++) install only after you accept them. Heavy tools operate
with a memory limit. Visual C++ 2017 needs wine on Linux and macOS (installing it says how to get wine).

## The export

**Export**, in the project's header, writes a usual decomp tree: matched functions as C, other functions as assembly, a linker script
and `build.py`. The export proves that the tree builds your game byte for byte. Game code and data are not in git.
The build makes them again from your copy of the game. GameCube and Wii projects are dtk-template projects. They
build the game again on each build.

## Installation details

On Linux, the installer installs [uv](https://docs.astral.sh/uv/), the app, the compilers and the m2c draft
decompiler. On Windows, the release MSI bundles Python and the app. All compilers are pinned and have
checksums. To start the app, use your apps menu or type `decomp` in a terminal.

**The .deb, the .rpm and the AppImage.** Each [release](https://github.com/n-delic/decompdecomp/releases) also has a
`.deb` (`sudo apt install ./decompdecomp_*.deb`), an `.rpm` (`sudo dnf install ./decompdecomp-*.rpm`) and an AppImage
for x86_64 Linux. They contain their own Python and uv. The app's Setup gets the compilers. These copies do not
update themselves: the app tells you when a new version is available, and you download it from the releases.

**Windows.** The release setup bundle installs missing WebView2 and .NET Framework 4.8 runtimes, then opens the app
installer, which lets you choose an installation folder and whether to add a desktop shortcut. Linux-only period
compilers run through WSL 2; run `wsl --install` in an administrator PowerShell, restart if asked, and open Ubuntu
once before importing a game that needs one.

**macOS (Intel and Apple Silicon).** Install the current source with
`curl -fsSL https://decomp.ndelic.com/install.sh | bash -s -- --source branch:main` until a Mac-capable release is
tagged. The installer creates `~/Applications/decompdecomp.app`; the app uses a native Cocoa/WebKit window and
Finder file dialogs. On Apple Silicon, the pinned Intel KMC compiler and binutils need Rosetta 2: run
`softwareupdate --install-rosetta` and accept Apple's license if you agree. PS1/PS2 period compilers are Linux-only,
and there is no macOS menu bar tray icon yet. Running `decomp` reopens the window while helpers continue.

**Its window and the tray.** decompdecomp opens in a window of its own. Linux uses GTK and WebKitGTK from your
system, macOS uses Cocoa and WebKit, and Windows uses WebView2. Only that window can open the app: its address does
nothing in a browser. There is only one
window: `decomp` or the apps menu shows the open one. When you close it while helper agents work, the app asks: stop
them and quit, or keep them working. They keep working without the window; the tray icon where available (or `decomp`) brings it
back. **Quit** ends the app and every helper agent it started.

**Updates.** When a new version is available, the app asks you one time. One click installs it and restarts the app.
Helper agents continue their work. To change this behavior, go to Settings.

## Your work

**Saving.** decompdecomp saves the work on each N64, PlayStation and PlayStation 2 project by itself as it changes, as plain
files in the project's `progress/` folder: matched C, best attempts, headers, names, notes, compiler ranges and
settings. It never saves the game. **Save now**, in the project's Settings, saves at once, also to another folder.
You can edit files in `progress/` or pull another contributor's changes into it; decompdecomp reads them on the next
project action and checks the saved functions again. `progress/` is the only part of a project to share or keep in git:
matched C in `src/`, best attempts in `best/`, the shared headers in `include/`, and `decompdecomp.json` (the game's
fingerprint, compiler, layout, names, notes and settings). The rest of the project's folder (the ROM, `asm/`, `assets/`, the
build files) is made from your copy of the game: keep it to yourself. To continue on another computer, copy the folder (or
push it to git) and click **New project → Open a saved project**. decompdecomp checks that your copy is the same game,
and judges every saved function again. GameCube, Wii and PC projects and public decomps keep their work in their own
git repository.

**Polish.** Matched C proves the code is right, but it starts raw: `func_80000A0C`, `D_8004AE30`, `arg0`, padded
structs and raw offsets. Once half of a ROM project matches, **Start agents** offers a second task: **Polish matched
code**. A polishing helper takes a matched function and renames it and what it uses across the project
(`rename_symbol`), describes structs, enums and constants in the shared header, names parameters and locals, and
writes one comment saying what the function does. A rewrite counts only when the function still matches byte for
byte; otherwise it's refused and the matching C stays. Starred functions go first, then the biggest (up to 400
instructions), where the logic is; stubs under 8 instructions are left alone, their names come with their callers.
The project shows two numbers: matched, and readable. Names are the agent's reading of the code, so check the ones
that matter to you.

**Notifications.** The bell beside the logo lists what happened in all your projects, each in its project's colour:
matches, helpers that stopped, what waits for you.

## Problems

If an import fails, the app shows the reason. **Copy for a bug report** copies the version, the error and the log for an [issue](https://github.com/n-delic/decompdecomp/issues).
**Report a problem**, at the bottom of the sidebar, opens a new issue in your browser with your version and system, and
copies a report with the log to paste into it. The full log is `~/Decomp/.decomp/logs/decomp.log`. Read
[CONTRIBUTING.md](../CONTRIBUTING.md) before you attach a log. Not sure it's a bug, or just stuck? Ask on [Discord](https://discord.gg/hhEm3zZagB).

## Development

Read [CONTRIBUTING.md](../CONTRIBUTING.md), [CONVENTIONS.md](CONVENTIONS.md) and [AGENTS.md](../AGENTS.md) before you make changes.

```bash
uv sync
uv run decomp              # the development copy: its own window, projects in ~/Decomp-dev (or $DECOMP_HOME)
uv run pytest              # the tests
```

| Path | Contents |
| :-- | :-- |
| `src/decompnet/rom/` | ROM mode: reading, splitting with splat, symbols, the matcher (`match.py`) and the export. |
| `src/decompnet/app/` | The app: `engine/` (ROM projects, a module per topic), `forkengine.py` (forks), `tools.py` (MCP tools), `agents.py` (starting and restarting helpers), `server.py` and `routes/` (the GUI's API, a module per part of the app), `collab.py` (rooms), `window.py` and `gtkwindow.py` (its own window), `tray.py` (the tray icon), `edition.py` (development copy or release), `static/` (GUI; its script is `static/js/`, a file per part), `guide/` (guides for agents). |
| `src/decompnet/agents/` | A module per agent (`claude.py`, `codex.py`, `antigravity.py`, `opencode.py`, `cursor.py`, `kimi.py`, `zcode.py`, `ohmy.py`, `api.py`): how a session starts, how to read what it prints, its models. |
| `src/decompnet/consoles/` | A folder per console (`n64/`, `ps1/`, `ps2/`, `gc_wii/`, `pc/`): its facts, its importer (`gc_wii/scratch.py` and `pc/scratch.py` start a project from scratch), its guides for agents. |
| `src/decompnet/forks/` | Fork mode: forks by repository address, project setup, objdiff, drafts and linked files. |
| `src/decompnet/network.py` | Claims, attempts, notes and the shared header. |
| `src/decompnet/classes.py` | C++ classes found in MIPS and x86 code: vtables, base classes, fields. |
| `src/decompnet/compilermap.py` | The compiler map: which compiler built each part of a MIPS game. |
| `src/decompnet/toolchains.py` | The pinned compilers and tools. |
| `packaging/linux/` | `build.sh` makes the `.deb`, the `.rpm` and the AppImage; `.github/workflows/release.yml` runs it for each `v*` tag. |
| `projects/demo-*/` | Homemade demo games for the tests. Each has its own `build.py`. |
| `site/` | The website, published to GitHub Pages at decomp.ndelic.com. `public/` holds the installers. |

The N64 demo game (`projects/demo-rom`) needs `ld.lld` and `llvm-objcopy` (Debian and Ubuntu: `apt install lld llvm`).

A checkout is "decompdecomp dev": a separate app with its own folder (`~/Decomp-dev`), port and window, so it never
touches the decompdecomp you have installed. For an experiment, give it a scratch folder:
`DECOMP_HOME=/some/scratch/folder uv run decomp`.
[ROADMAP.md](../ROADMAP.md) lists what's planned.

**Test a branch.** Run the installer from a checkout of the branch. The app then follows that branch on GitHub, and
each new commit shows as an update. A checkout of `main` updates on releases only, like a normal install.

```
git clone -b <branch> https://github.com/n-delic/decompdecomp && cd decompdecomp
bash site/public/install.sh
```

Without a checkout, set `DECOMP_BRANCH=<branch>` and run the installer from
`https://raw.githubusercontent.com/n-delic/decompdecomp/<branch>/site/public/`. `decomp update` installs the newest
commit.

## Built on

[splat](https://github.com/ethteck/splat) and [spimdisasm](https://github.com/Decompollaborate/spimdisasm) split
ROMs. [objdiff](https://github.com/encounter/objdiff) judges forks. [m2c](https://github.com/matt-kempster/m2c) and
[Ghidra](https://github.com/NationalSecurityAgency/ghidra) write drafts.
[decomp-permuter](https://github.com/simonlindholm/decomp-permuter) rewrites near misses. The
[decompals](https://github.com/decompals) period compilers build the code.
[decomp-toolkit](https://github.com/encounter/decomp-toolkit) splits GameCube and Wii games, and
[wibo](https://github.com/decompals/wibo) runs the Windows compilers on Linux. Thank you to the decomp community for
these tools. decompdecomp is not related to these projects.

### Reusing function names

The free matching pass also looks for useful names in other local ROM and imported projects. It only names
functions that still have generated names and have not been explicitly renamed. Donors must be matched,
share the processor and byte order, and have an identical relocation-normalized machine-code fingerprint.
Functions smaller than 32 bytes, conflicting donor names, duplicate target candidates, occupied names and
claimed functions are skipped. Existing names are preserved. Every rename uses the normal regression checks.

Function details distinguish generated names, named functions and recorded renames. An automatic transfer
is labelled **inferred**, with its donor project: identical machine code does not prove an original source
name or identical purpose. New renames retain their original name and provenance; older names are recognized
as named, without inventing a rename history that was never recorded.

### Detecting libultra and naming library routines

Nintendo 64 ROM projects show **Libultra (header)** in project settings. This decodes the SDK declaration
at ROM offset `0x0C`; it does not establish the version of every linked library object.

In **Local SDK signatures**, index your own `.a` or `.o` files with a corpus label such as
`libultra 2.0I / libgultra_rom.a`. Keep releases and IDO/GCC, debug and retail variants separate.
Settings list the strongest exact fingerprint matches and the fingerprints specific to a release
**among the archives you indexed**. Missing variants, shared objects and developer modifications can
prevent an exact version determination. A leading candidate is evidence, not a confirmed version.

The free pass also names generated, unclaimed functions when a signature at least 32 bytes long has one
name across the indexed library variants and one target in the game. Compiler builtin names such as `memcpy` are excluded because the spelling can change compilation.
Name collisions and explicit prior renames are preserved. The normal checked rename operation verifies existing matched C before saving.
Function naming metadata and notes retain the source corpora and fingerprint. Naming a routine does not
increase the decompilation match count. No library archive or source is bundled or downloaded.

## Agent token use

Agent tools now return compact project facts, claim assembly and compiler feedback. Complete function briefs
remain available through `get_function`; `project_info(detail=true)` returns all project facts. Detailed guides
and long tool instructions load through `guide(topic)` and `guide(topic="tool:compile_patch")` when needed.

For a small source change, an agent can call `get_candidate(function)` once, then use
`compile_patch(function, revision, old, new)`. The old text must occur exactly once and the revision must still
identify the current candidate. Each response supplies the next revision. A stale or ambiguous patch is refused;
a valid patch goes through the same claim limits, compiler, build protection and byte judge as a full submission.
`compile_and_diff` remains available for a new function or larger rewrite, and both tools accept optional repair
notes on a verified match.

Compile feedback shows a bounded set of changed instruction rows. An equal score alone never means the diff is
unchanged. `last_diff(function, start=0, limit=40)` pages the saved full diff without compiling, and `get_assembly`
pages the target listing. Cached submissions return a short result and do not consume another compile attempt.
Claims include a small historical summary across agents, recent source-change summaries and the number of
recorded automatic repair/reuse trials. Historical scores can reflect different headers or compiler settings.

The model-cost panel separates reported uncached input, cache reads, cache writes and output. These are token
categories, not interchangeable prices. Older records without a breakdown say it is unavailable. Providers'
reported dollar amounts remain separate; no cost estimate is inferred from total tokens alone.


Small agent edits use `compile_patch`: fetch the candidate once, then retain the edited C and
reuse the revision returned by each compile. Fetch again only if the revision is stale or the
agent lost the source. Full submissions remain available for rewrites. Claims keep candidate C,
compiler facts and declarations; supporting notes, similarity details, layouts and attempt history
are available through `get_function` when needed. Codex errors rejecting a reasoning effort pause
the helper immediately; change the model or effort before restarting it.

### Agent names, history and estimated costs

For an existing CLI agent, open its **⋮ → Change model** action, choose a model and press
**Replace agent**. The replacement keeps its task, account and work settings. The original
stops after creation succeeds and remains in Agents with its history and credited work.
OpenCode's model picker groups **OpenCode Go** separately from **OpenCode**; select a Go
model to use the Go account configured in the CLI.

Use **Rename** on an agent card to give it a display name. Its internal identity, claims and
credited work stay unchanged, and the name survives app restarts. **Add agents** creates fresh
identities with fresh counts; **Start again** resumes a stopped agent and keeps its history.
Removing a card preserves historical project and model totals.

Codex cards can show **API-equivalent** dollars and dollars per match, calculated from separately
recorded input, cached-input and output tokens. These estimates use Standard API rates checked
on 26 October 2026 ([OpenAI pricing](https://developers.openai.com/api/docs/pricing), with older
models' model pages). They are not subscription charges or additional money owed. Cache-write,
long-context, Fast-mode and regional premiums are excluded. Unknown models or incomplete legacy
token breakdowns have no estimate; reported provider charges remain separate.


### Fewer model round trips for small experiments

`compile_variants(function, revision, patches)` tests up to six independent `{"old": "...",
"new": "..."}` replacements against the same candidate snapshot. Each uses the normal compiler,
claim limits and exact judge. All replacements are validated before trials begin. Trials stop on
a match or claim error, and do not start another trial after 60 seconds; results give a compact summary and the current revision. Patches are not
cumulative. The current candidate is not automatically restored to the best trial; cached or
rejected trials may retain an earlier candidate. Best attempts remain saved normally. Use
`get_candidate` if you need to recover the current C, or `last_diff` for the last completed trial.

Claims include the candidate revision when their C is exactly the patchable candidate. If a brief
only shows a fragment of a larger source file, fetch the full candidate before patching.
`get_function(section="notes")` fetches supporting notes without the complete brief; `history`,
`candidate`, `declarations`, `compiler` and `layouts` are also available, with `all` preserving the
full view. After three successive identical instruction diffs, feedback suggests changing approach
or trying a local permutation search. This advice does not skip attempts or change the judge.


Batch results group variants with identical displayed instruction diffs and scores into one line;
this is display compression, not proof of identical bytes and never skips the normal judge.
Where available, results compare scores with a previous compile of the starting C and count changed
displayed diff rows. Missing baselines are marked unavailable; historical compiler/header settings
may differ. Equal scores with different diffs remain separate, and compile errors stay distinct.

`compile_family(function, revision, family, old)` generates local experiments without sending each
replacement. `casts` wraps expression `old` in int, unsigned int, short, unsigned short, char and
unsigned char casts. `signedness` toggles the integer type at the start of an exact declaration
anchor (standard integer types or s8/u8 through s64/u64). `declaration_order` swaps exactly two
adjacent simple uninitialized declarations. The generated replacements are shown with their variant
numbers. All use the same six-trial/60-second batch limits and normal claim, compiler and judge paths.

Claims include a bounded summary of recent recorded batch families and their best historical scores,
shared across agents. `get_function(section="history")` includes this summary. These records are
observations, not a permanent exhausted-family blacklist; later headers and compiler settings may
make another trial useful. Records contain family names and summary metrics, not game source.


### Choosing local experiment targets

For `compile_family`, use `declaration="counter"` with `signedness` or `declaration_order` to
select a unique simple uninitialized declaration by name. The latter swaps it with the immediately
following supported declaration. Initialized, complex, duplicate-name and comment-only examples
are excluded. Claims suggest at most two applicable declaration experiments. Missing or invalid
targets return at most six declaration choices with line numbers instead of requiring a full-source read.

For repeated expressions, use `old="value"` and `occurrence=2` to change only the second exact,
nonoverlapping occurrence. Occurrences count literal text throughout the candidate, including
comments and declarations: use a larger anchor for surrounding context when needed. This applies
to `compile_family`, `compile_patch`, and each patch in `compile_variants`. Omitted/zero occurrence
still requires a unique anchor; all edits retain revision checks and the normal judge. Ambiguous
anchors return occurrence counts and up to six line numbers.

Compact claims defer shared-header declaration fences longer than 16 lines to
`get_function(section="declarations")` / `get_context(function)`, and reference identical declaration
blocks once within a batch. Candidate C and user instructions remain intact. Repeated generated
compilation instructions are shown once, and the recent-family summary is limited to two families;
full function and history reads remain available.


### Current targets and automatic selection

`family_targets(function, family="declaration_order")` returns the current candidate revision and
up to six valid declaration pairs, with tried/untried status. `family="signedness"` lists only
supported unique integer declarations. `expression="value"` also lists the count and first six line
numbers of literal occurrences; use `family="casts"` when only expression locations are needed.
This is a read-only operation: no full source download or compile. Error choices and claim hints use
the same validity filter, so a variable without a supported adjacent declaration is not offered as a
swap target.

`compile_family(function, revision, family="declaration_order", target="next")` selects the first
valid untried pair locally, or reports that none remain without compiling. Signedness supports the
same selection. Do not combine `target="next"` with `old`, `declaration` or `occurrence`. Existing
revision checks, the exact judge, attempt caps and batch budgets still apply. A target is recorded
only after at least one trial was submitted. Tried status is scoped to the candidate revision,
project configuration and context versions; it does not blacklist targets after a compiler/header
change. Explicit declaration/anchor requests remain available for deliberate retries. Cast expressions
remain agent-selected rather than guessed automatically.

`redraft` requires actual nonempty C declarations in `context`; it is not a refresh command. Use
`get_function(section="candidate")` to inspect the existing C. Compiler and flag findings belong in
`compiler_map`, `try_compiler`/`search_flags`, or `suggest` for proposed setup changes, not
`share_knowledge`. Invalid requests explain the appropriate tool and perform no drafting or knowledge write.


Captured repair patterns now run in the deterministic draft-repair round before random permutation.
Saved m2c argument-position refinements run first; bounded typed temporaries, branch orientation,
narrowing and known-debug-flag source-line variants follow, within the existing 32-compile/20-second
round budget and rotating function queue. Agents can also request `compile_family` with
`typed_temporary`, `branch_orientation`, `narrowing`, or `source_lines` and just function/revision.
Imported projects share the source patterns and normal objdiff judge; automatic source-line trials
await recorded per-file debug flags (ROADMAP).

New repair captures prefer a failed candidate with matching recorded settings and retain the last
failed trial by the winning agent separately, including its source/header and diff in full exports.
ROM submissions record the selected compiler configuration and command template at compilation,
including failures. Imported builds still explicitly mark effective per-file flags as unavailable.
Agent batches/families attach family, variant and base revision provenance automatically; deterministic
background repairs attach candidate fingerprints. The evidence remains observational and cannot
prove a particular edit caused a match. Older captures are unchanged.

### Kimi Code helpers

Select **Kimi Code** under **Start agents** after installing the CLI and signing in with `kimi login`.
The desktop app and CLI share the account configuration. Models come from your Kimi Code model list;
choose its default or another configured alias. Effort options are low, high and max, subject to model support.

Each helper uses a private Kimi data directory with copies of its login credentials and provider/model
settings. Only decompdecomp's MCP tools are enabled: user MCP servers, hooks, plugins and built-in shell,
file and web tools are not enabled. Prompts arrive on stdin through a Node launcher, rather than OS argv.
Your original Kimi settings are not edited. If copied credentials expire, sign in again and restart the helper.

Text and tool activity stream into the agent feed. At normal session exit, request usage is read from
that session's local `usage.record` entries, keeping input, cache reads, cache writes and output separate.
Context occupancy is never counted as spend, and duplicate step summaries are ignored. Abrupt termination
can leave usage unreported. Kimi subscription usage has no invented dollar charge. Ordinary claiming,
matching, per-session limits, stopping, restarting and model statistics use the shared helper pipeline.

Validated locally with Kimi Code 2.3.3 against an isolated HTTP MCP server using an existing desktop login.
The CLI called the test tool and returned its result; its token breakdown was recorded. No game project
or live helper was used for that smoke test.
