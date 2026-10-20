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
   repository. decompdecomp shows what the
   project says about AI, then clones it to your computer, on a local `decomp-wtf` branch.
2. **Add your copy of the game.** The app knows the usual project layouts (dtk-template, configure.py and ninja,
   splat and make). It puts your copy in the correct location and runs the setup of the project. For other layouts,
   follow the README of the project.
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

## Features

| Feature | Description |
| :-- | :-- |
| **Live view** | A map of all the functions in the game. You see the tool calls and the scores of each agent in real time. |
| **Autopilot** | Matches without cost. Code that occurs more than one time gets the C of its matched twin. Then machine drafts go to the judge. (Mario Party 6: 762 functions in 12 minutes.) |
| **Permuter** | Rewrites near misses (at most 6% of the function differs) thousands of ways in the background, with decomp-permuter (install it in Compilers & tools). It uses spare cores at a low priority. The judge decides what matches. Without decomp-permuter, a few simple rewrites run instead. |
| **Batches** | Agents write many small functions in one turn. decompdecomp judges them in one call. |
| **Editor** | Each function has an editor. Write C, push Ctrl+Enter and look at the diff. |
| **Suggestions** | Agents can suggest a different compiler, for the full game or for address ranges. decompdecomp tests each suggestion first. You apply it with one click, or let proven suggestions apply automatically (a setting of each game). |
| **Flag search** | `search_flags` builds a function again with each flag of its compiler changed, one at a time (up to 12 builds), and says which change gets closer. MIPS for now. |
| **Shared header** | Structs and prototypes go into one header. decompdecomp accepts a change only if all matches stay matches. |
| **Safe names** | Matches use addresses. A new name for a function or data cannot break a match. |
| **Look-alikes** | `find_similar` compares instruction sequences across the full game and finds copied and inlined code. |
| **Compiler map** | Big games mix compilers. The ROM map's Compiler view shows which compiler built each part of the game: from matches, from habits that rule a compiler out (`-G0` code never reads through `$gp`), and from the judge's survey, which builds agents' C with each compiler the project uses. A part the judge confirms becomes a suggestion. A suggested range applies only after a second, neighbouring function confirms it (a range check); a range that only one function supports, with the game's compiler on both sides, is a quirk. decompdecomp checks the ranges a project already has again by itself. Agents read it with `compiler_map`. MIPS for now. |
| **Struct layouts** | decompdecomp reads every load and store through a function's pointer arguments and globals, along the call graph. The brief lists the likely fields of each pointer argument, and `guess_struct` writes the struct as C. MIPS (PlayStation 2, N64) for now. |
| **Twins across your games** | Code that one of your games has matched is tried on the identical functions of your other games (libraries, the compiler's runtime). No agent time. |
| **Shared knowledge** | Agents propose what they learn about a game. A proposal counts when helpers of two different models back it. Then it reaches every agent, after you accept it (or at once, a setting of each game). Run helpers of several models for this. |
| **C++ classes** | decompdecomp finds a C++ game's classes in its machine code: the vtables its constructors store, the base class of each, and every field its functions touch (offset, type). Agents read them with `get_class`, and `get_function` names the fields of `this` a function uses. MIPS (PlayStation 2, N64) for now. |
| **Drafts** | m2c for MIPS, PowerPC and ARM. Ghidra (optional) for x86, AArch64 and other architectures. |
| **Guides** | Agents get guides for each architecture and compiler, and for C++ and objdiff. |
| **Disc formats** | ROMs, PlayStation 2 discs (ISO, BIN, CHD), GameCube and Wii discs (ISO, GCM, RVZ, WIA, WBFS, CISO, GCZ) and program files. |

## Agents

| Agent | Connection |
| :-- | :-- |
| **API key** (recommended) | The main helper. The app talks to the model's API itself, with your key: nothing else to install, and no other program's update can break it. Each OpenAI-compatible API works: DeepSeek, OpenRouter, OpenAI, or a local model with llama.cpp, Ollama or LM Studio. |
| **Claude Code** | Operates without a window, through the local MCP server of the app. |
| **Codex CLI** | The same. It can approve only the tools of decompdecomp. |
| **Antigravity CLI** | The same, through the MCP configuration of the workspace (`agy`). |
| **opencode** | The same, with each provider that you set up: DeepSeek, Qwen, Kimi and more. |

The app starts each helper itself, from **Start agents** in the project's header. A helper that stops starts again
by itself. On a usage limit it waits until the limit resets. Five fast failures in a row pause it, and the app says
why. Run several helpers, of different models: they work on different functions at the same time.

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
| **Nintendo 64** | ★ **Recommended** | ✓ ROM | KMC GCC 2.7.2 · IDO 5.3 / 7.1 | ✓ export | ✓ |
| **PlayStation 2** | Beta | ✓ disc or ELF | EE-GCC 2.9 · 2.95.2/2.95.3 (SN) · 2.96 · 3.2 · CodeWarrior 2.3.3 / 3.0.1 | ✓ export | ✓ |
| **GameCube · Wii** | Beta | ✓ disc (ISO, RVZ, WBFS…) or DOL | CodeWarrior GC 1.0 – Wii 1.7 | ✓ dtk-template | ✓ |
| **Windows PC** | Beta | ✓ program (32-bit x86 .exe) | Visual C++ 6.0 · 7.0 · 7.1 | — | ✓ |
| **Nintendo DS** | Beta | ✓ ROM (.nds) | CodeWarrior for DS 1.2 – DSi 1.6 (24 versions), one per source file | ✓ ds-decomp | ✓ |
| **PlayStation · GBA · 3DS · PSP · Switch · Xbox · Xbox 360** | Public decomps only | | | | ✓ |

[ROADMAP.md](../ROADMAP.md) has a table of which feature works on which platform.

The tests below were made before 1.0.3, which rewrote most of the app: every platform needs testing again. Try
it on a game you own and say how it went; that's how a platform goes from beta to recommended.

**Recommended: Nintendo 64.** It is tested with Super Mario 64, GoldenEye and Perfect Dark, from the import to a
game rebuilt byte for byte. This includes code that the game loads later and compressed code. The export puts each
function at its original address. This is correct for matches and ports. Mods that make code larger need a
different build, which is planned.

**Beta:**

- **PlayStation 2** is tested on real games (GTA San Andreas, Ridge Racer V), with several compilers per game. No
  real game is rebuilt whole yet.
- **GameCube and Wii** projects are dtk-template projects, as most community decomps are. Each build makes the game
  again and compares it byte for byte. The homemade demo game passes every step, but no real game is tested from
  start to end yet.
- **Windows PC** passes every step on the homemade demo game. It is tested on few real games.

Please [report problems](https://github.com/n-delic/decompdecomp/issues). The status of a public decomp is the status
of its own project.

**Tools for a start from scratch:**

| Platform | Tool |
| :-- | :-- |
| N64, PS2 | splat and spimdisasm. They also find code that the game loads later, and overlays. |
| GameCube, Wii | decomp-toolkit, in a [dtk-template](https://github.com/encounter/dtk-template) project (`consoles/gc_wii/scratch.py`) |
| Windows PC | [Ghidra](https://github.com/NationalSecurityAgency/ghidra). decompdecomp cuts each function out of the .exe into a project for Visual C++ and objdiff (`consoles/pc/scratch.py`). |

**C and C++.** GameCube, Wii, PS2 and Windows PC games are often C++. On these platforms, agents can write
classes and methods, and decompdecomp changes the C++ names back to the names of the game. N64 games use C. Public decomps use the language of their project.

**Proprietary compilers** (IDO, CodeWarrior, Visual C++) install only after you accept them. Heavy tools operate
with a memory limit.

## The export

**Export**, in the project's header, writes a usual decomp tree: matched functions as C, other functions as assembly, a linker script
and `build.py`. The export proves that the tree builds your game byte for byte. Game code and data are not in git.
The build makes them again from your copy of the game. GameCube and Wii projects are dtk-template projects. They
build the game again on each build.

## Installation details

The installer installs [uv](https://docs.astral.sh/uv/), the app, the compilers and the m2c draft decompiler. All
compilers are pinned and have checksums. To start the app, use your apps menu or type `decomp` in a terminal.

**The .deb, the .rpm and the AppImage.** Each [release](https://github.com/n-delic/decompdecomp/releases) also has a
`.deb` (`sudo apt install ./decompdecomp_*.deb`), an `.rpm` (`sudo dnf install ./decompdecomp-*.rpm`) and an AppImage
for x86_64 Linux. They contain their own Python and uv. The app's Setup gets the compilers. These copies do not
update themselves: the app tells you when a new version is available, and you download it from the releases.

**Linux only.** This version runs on Linux. Windows and macOS are not supported in this version. On Windows 11,
the Linux version may work inside WSL (WSLg shows its window), but nobody tests that.

**Its window and the tray.** decompdecomp opens in a window of its own. It uses GTK and WebKitGTK from your
system, not a browser. The installer and the packages install them; without them, `decomp` tells you the command
for your distribution. Only that window can open the app: its address does nothing in a browser. There is only one
window: `decomp` or the apps menu shows the open one. When you close it while helper agents work, the app asks: stop
them and quit, or keep them working. They keep working without the window; the tray icon (or `decomp`) brings it
back. **Quit** ends the app and every helper agent it started.

**Updates.** When a new version is available, the app asks you one time. One click installs it and restarts the app.
Helper agents continue their work. To change this behavior, go to Settings.

## Your work

**Saving.** decompdecomp saves the work on each N64 and PlayStation 2 project by itself, every few minutes, as plain
files in the project's `progress/` folder: matched C, best attempts, headers, names, notes, compiler ranges and
settings. It never saves the game. **Save now**, in the project's Settings, saves at once, also to another folder.
`progress/` is the only part of a project to share or keep in git: matched C in `src/`, best attempts in `best/`, the
shared headers in `include/`, and `decompdecomp.json` (the game's fingerprint, compiler, layout, names, notes and
settings). The rest of the project's folder (the ROM, `asm/`, `assets/`, the build files) is made from your copy of
the game: keep it to yourself.
To continue on another computer, copy the folder (or push it to git) and click **New project → Open a saved
project**. decompdecomp checks that your copy is the same game, and judges every saved function again. GameCube, Wii
and PC projects and public decomps keep their work in their own git repository.

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

If an import fails, the app shows the reason. **Copy for a bug report** copies the version, the error and the log
for an [issue](https://github.com/n-delic/decompdecomp/issues). **Report a problem**, at the bottom of the sidebar,
opens a new issue in your browser with your version and system, and copies a report with the log to paste into it.
The full log is `~/Decomp/.decomp/logs/decomp.log`.
Read [CONTRIBUTING.md](../CONTRIBUTING.md) before you attach a log. Not sure it's a bug, or just stuck? Ask on
[Discord](https://discord.gg/hhEm3zZagB).

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
| `src/decompnet/agents/` | A module per agent (`claude.py`, `codex.py`, `antigravity.py`, `opencode.py`, `api.py`): how a session starts, how to read what it prints, its models. |
| `src/decompnet/consoles/` | A folder per console (`n64/`, `ps2/`, `gc_wii/`, `pc/`): its facts, its importer (`gc_wii/scratch.py` and `pc/scratch.py` start a project from scratch), its guides for agents. |
| `src/decompnet/forks/` | Fork mode: forks by repository address, project setup, objdiff, drafts and linked files. |
| `src/decompnet/network.py` | Claims, attempts, notes and the shared header. |
| `src/decompnet/classes.py` | C++ classes found in MIPS code: vtables, base classes, fields. |
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
