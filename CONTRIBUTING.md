# Contributing to decompdecomp

Thanks for helping. Bug reports, fixes, new platforms and better guides for agents are all welcome.

## The one rule: no game data

Never put any part of a game in an issue, a pull request, a test or a screenshot: no ROMs, disc images or
executables, and no code, assembly or assets taken from them. That includes the C your agents matched. It comes from
the game, and it belongs to you and the game's owner, not to this repository. Sam's request from decomp.wtf still
stands: only use decompdecomp on games you own, and respect the rules of the decomp projects you work with.

Before you attach a log (`~/Decomp/.decomp/logs/decomp.log`), read it, and cut out any code or assembly.

## Reporting a bug

[Open an issue](https://github.com/n-delic/decompdecomp/issues) with:

- your version (bottom left of the app, or `decomp update --check`) and your Linux distribution;
- the platform (N64, PS2, GameCube, PC, ...) and the compiler the project uses;
- what you did, what you expected, and what happened instead, with the error text;
- the part of the log around it, if it helps (see above).

The name of the game is fine to mention. Many bugs only show up on one game, and the name helps a lot.

## Working on the code

decompdecomp is Python 3.12+, managed with [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/n-delic/decompdecomp && cd decompdecomp
uv sync
uv run decomp                 # the app, in your browser (projects in ~/Decomp, or $DECOMP_HOME)
uv run pytest -q              # the tests
uv run pytest tests/test_ps2.py -q   # one file
uv run ruff check src tests projects   # the linter
```

If you use decompdecomp yourself, keep your own copy out of your tests: run the checkout with
`DECOMP_HOME=/some/scratch/folder uv run decomp`. A newer version that finds your installed app running replaces it.
The tests already use a folder of their own.

Compilers and tools (splat, objdiff, dtk, wibo, Ghidra and the period compilers) are pinned with checksums in
`src/decompnet/toolchains.toml`, and the app installs them in Setup. Many tests skip when a compiler isn't installed,
and that's fine. The homemade demo games in `projects/demo-*` are built by their own scripts. The N64 demos
also need `ld.lld`, `llvm-objcopy` and `llvm-nm` on your PATH (Debian and Ubuntu: `apt install lld llvm`).

**[docs/CONVENTIONS.md](docs/CONVENTIONS.md) has the house rules, and [AGENTS.md](AGENTS.md) how the code is laid
out. Read them first.** In short:

- Match the code around you: plain-English docstrings and comments that say *why*.
- Every change in behaviour comes with a test in `tests/`. A test that needs a real game or a proprietary compiler
  skips when it isn't there; use or extend a demo game instead.
- Don't add a dependency for a few lines. capstone, pyelftools and httpx are already there.
- Errors shown to users or agents are sentences they can act on.
- `uv run ruff check src tests projects` passes. There's no formatter: don't reformat code you didn't change.

Some things we won't merge:

- Anything that weakens the judge. A match means the bytes are identical after linking (ROM mode), or objdiff
  says 100% (fork mode).
- Downloading a proprietary compiler without the user agreeing to it in Setup, or taking tools from PATH instead of
  the pinned ones.
- Letting agents edit a project's build setup. They're limited to source and header files on purpose.
- Links to the decomp.wtf website. That domain isn't ours. Keep the internal names (`decomp`, `decompnet`,
  `~/Decomp`, `.decomp.wtf.json`): existing installs depend on them.

Not sure what to work on? [ROADMAP.md](ROADMAP.md) lists what's planned and why, and has a table of which feature
works on which platform: every — in it is a gap to fill.

## Consoles

### Where a console's code is

Each console has a folder: `src/decompnet/consoles/<id>/`. Everything decompdecomp knows about that console is in
it. The rest of the app never asks "is this a PS2 game?". It asks the project's console, `consoles.get(platform)`,
through one file: the connector, `consoles/__init__.py`.

| Folder | Console | Mode | Its files |
| :-- | :-- | :-- | :-- |
| `consoles/n64/` | Nintendo 64 | ROM | `rom.py` the ROM file and its header · `code.py` code the game loads later, compressed code · `guides/` |
| `consoles/ps2/` | PlayStation 2 | ROM | `elf.py` the game's program · `disc.py`, `chd.py` disc images · `overlays.py` code in the disc's other files · `syscalls.py` kernel calls · `symtab.py`, `mdebug.py`, `stabs.py`, `cheader.py`, `debuginfo.py` the game's own debug symbols · `guides/` |
| `consoles/gc_wii/` | GameCube, Wii | PROJECT | `scratch.py` a dtk-template project for the game · `guides/` |
| `consoles/pc/` | Windows PC | PROJECT | `scratch.py` Ghidra finds the functions, decompdecomp cuts them out of the .exe · `project_template/` · `guides/` |

Each folder's `__init__.py` makes its console: the facts and the parts the app calls. Start there. Its docstring
lists its files.

Around them:

| Where | What |
| :-- | :-- |
| `consoles/__init__.py` | The connector: the `Console` class (every fact and part, one line each) and the list of consoles. |
| `consoles/public.py` | Consoles only public decomps cover: a name, a processor, m2c's target, guides. |
| `consoles/template/` | The package to copy for a new console. |
| `arch/` | What depends only on the processor: `mips.py`, `ppc.py`, `x86.py`, `arm.py` (relocations, disassembly, hand-written assembly, which readers exist). |
| `rom/` | ROM mode, shared: `split.py` (splat), `match.py` (the judge: link and compare), `export.py`. |
| `forks/`, `app/forkengine.py` | PROJECT mode and public decomps, shared: the project's build, objdiff. |

A console works in one of two modes:

- **ROM**: decompdecomp splits the game with splat and judges each function by linking it at its real address and
  comparing the bytes. The console reads its file and says its compilers. The judge reads MIPS today.
- **PROJECT**: decompdecomp makes the community's kind of project for the game (a git tree with `objdiff.json`), and
  objdiff judges each function in the project's own build. The console makes the project.

### Improving a console

1. Open `consoles/<id>/__init__.py`. Its docstring says which file does what.
2. Change the part you need in that folder. A fact about the processor, not the console (how MIPS code is read),
   goes in `arch/<processor>.py`. Something every console of a mode needs goes in `rom/` or `forks/`.
3. Run its tests: `uv run pytest tests/test_consoles.py -q -k <id>`, and its own file (`tests/test_ps2.py`, ...).
4. When a feature reaches the console, tick its column in ROADMAP.md's table.

### Adding a console

1. **Copy the template.** `cp -r src/decompnet/consoles/template src/decompnet/consoles/<id>`. Rename the class and
   the object at the end of its `__init__.py`.
2. **Pick its mode.** ROM if splat splits its programs (MIPS today). PROJECT if the community has a project layout
   and a delinker for it, judged by objdiff (see `consoles/gc_wii/` and `consoles/pc/`).
3. **Fill in its facts.** Each is one line in the `Console` class:

   | Fact | What it is |
   | :-- | :-- |
   | `id`, `aliases` | What project configs store. Keep it once projects exist; old ids go in `aliases`. |
   | `name`, `short` | What the app shows, and where space is short. |
   | `status` | `BETA` until its conformance test passes and a real game rebuilt byte for byte; then `RECOMMENDED`. |
   | `arch`, `big_endian` | Its processor, an id in `arch/`. |
   | `mode`, `layout` | ROM or PROJECT, and what a project of it is ("splat", "dtk-template"). |
   | `compilers` | Its compilers (`Compiler(id, name, built, guess)`). The first is the default. |
   | `packs` | More toolchains Setup offers for it, with what each is for (a proprietary compiler pack). |
   | `guides`, `compiler_guide` | Guides agents read first; the one every helper gets up front. |
   | `compiler_family` | Its compilers' family ("mwcc") when a project names no toolchain: the permuter's weights. |
   | `m2c` | m2c's target for drafts ("mipsee-gcc"), or "" when m2c can't read its code. |
   | `ram` | Where its programs live: words in this range are read as pointers. |
   | `demo`, `demo_tools` | Its demo game's folder in `projects/`, and the tools building it needs. |
   | `own_features` | `EXPORT` (a tree that rebuilds the game), `REBUILD_CHECK` (the project's rebuild check). |
   | `as_flags`, `image_ext`, `asm_preamble` | ROM mode's export: assembler flags, the image's extension. |

4. **Fill in the parts it needs.** The rest keep their default, which does nothing or says it can't:

   | Part | What it does |
   | :-- | :-- |
   | `sniff(head, file)` | Says whether a file is this console's game, from its first bytes. Be strict. |
   | `import_game(...)` | Starts a project from the user's copy. The mode's way by default. |
   | `read_game(file, name)` | ROM: the program to split, with its header's facts (title, sha1, compiler guess). |
   | `default_compiler(info, splat_guess)` | Which compiler built the game. |
   | `default_flags(compiler, gp)` | A compiler's flags for a game of this console. |
   | `add_code(config, ...)` | ROM: code splat misses (segments loaded later, overlays), added before it splits. |
   | `find_gp(image, program, segments)` | ROM: `$gp` as the game sets it. |
   | `debug_info(program, known)`, `debug_brief(known)` | The game's own symbols and types, and their lines in a brief. |
   | `after_import(net, slug, chosen)`, `on_start(net, slug)` | Work a new project gets once; work each project gets at start. |
   | `create_project(root, game, compiler)`, `game_info(game)` | PROJECT: makes the project; says which game a file is. |
   | `tool_args()` | PROJECT: points the project's configure step at the pinned tools. |
   | `identity(path)`, `check_rebuild(fp, version)`, `read_data(fp, name, length)`, `upload_name(data)` | PROJECT: rooms' game check, the rebuild check, data the objects lack, an upload's file name. |
   | `asm_reason(code)` | Why a function can't come from a compiler. The processor's check, with this console's facts. |
   | `up_front_guide(compiler)`, `m2c_target(compiler)` | The guide for a compiler's code; m2c's target for it. |

5. **Its compilers.** One pinned entry each in `toolchains.toml` (see "Adding a compiler" below).
6. **Its processor**, if `arch/` hasn't got it: add `arch/<name>.py` (relocation masks, capstone's mode, and
   `asm_reason` if you can tell hand-written code) and its `Arch` in `arch/__init__.py`.
7. **Its guides** for agents: `consoles/<id>/guides/<topic>.md`. The first line is the guide's title.
8. **A demo game**: `projects/demo-<id>/build.py` builds the Demo Quest game (`projects/demo-n64/original`) with the
   console's compiler, and writes `build/demo.json`: `{"game": <file>, "symbols": {name: address}}`. Use only code
   we may share. Never any part of a real game.
9. **Register it** in the list at the end of `consoles/__init__.py` (`_registry`).
10. **Pass the conformance test**: `uv run pytest tests/test_consoles.py -q -k <id>`. It builds the demo, checks
    `sniff`, imports it, judges matching C (score 0) and wrong C, makes a draft, and rebuilds the game where the
    console can. It skips, saying what's missing, when a tool isn't installed.
11. **Say it**: add a column to ROADMAP.md's table, and the console to README.md's and docs/GUIDE.md's lists.
12. The drop zone takes a quick look at a file before it uploads it (`app/static/js/home.js`, `identifyHead`).
    Add your console's file there too, or the page refuses its files.

### Adding a compiler to a console

Example: PC has Visual C++ 6.0, 7.0 and 7.1, and you want Visual C++ 2005 (8.0).

1. **Pin it** in `src/decompnet/toolchains.toml`. A new part of the `msvc` pack (or a pack of its own) with its
   `url`, its `sha256` (`sha256sum` of the archive) and `into = "8.0"`. Take it from where the decomp community
   keeps it; never from the vendor's site with someone's account.
2. **Proprietary means opt-in.** Its entry says `license = "proprietary"`. The app installs it only after the user
   accepts its licence in Setup. Never download it in code, a test or CI: public CI can't have it, and its tests
   skip there.
3. **How it runs on Linux.** Windows compilers run through wibo (a pinned tool); check that the new `cl.exe` runs
   there. A Linux compiler needs nothing; a 32-bit one may need `requires = ["runtime-i386"]`.
4. **The console lists it**: `consoles/pc/__init__.py`, `MSVC_VERSIONS`. A ROM console's compilers are its
   `compilers` tuple: `Compiler(id, name, built, guess)` (see `consoles/ps2/__init__.py`).
5. **Which game used it.** PC reads the program's Rich header: add 8.0's build numbers to `RICH_BUILDS` in
   `consoles/pc/scratch.py`. A PS2 program's `.comment` note says its GCC: `_GCC` in `consoles/ps2/elf.py`, and the
   compiler's `guess` names it.
6. **Its flags.** The defaults are the toolchain's `flags`, and the console's `default_flags` adjusts them (the PS2's
   small data). PC projects write theirs in `project_template/tools/configure.py` (`CL_FLAGS`; from 8.0 on, `/GX`
   is `/EHsc`).
7. **MIPS only**: `search_flags` tries flags by compiler family (`FLAG_FAMILIES` in `app/engine/compilers.py`), and
   the compiler map tells compilers apart by their habits (`compilermap.py`, `quirks`). Add the new family there.
8. **Prove it builds matching code**: build the demo with it (`projects/demo-ps2/build.py <toolchain>` does this
   for the PS2's compilers) and add it to that console's test (`OTHER_COMPILERS` in `tests/test_ps2.py`), skipped
   when it isn't installed: `pytest.mark.skipif(toolchains.installed(...) is None, reason="needs ...")`.
9. **Say it**: the compiler in ROADMAP.md, README.md and docs/GUIDE.md where they list the console's compilers.

## Pull requests

- Keep each one to one change, and say in the description what it fixes or adds, and how you tested it.
- Commit messages describe what users get, in one sentence, with a body explaining why.
- Don't bump the version. Releases are made separately.
- Pull requests written with an AI agent are fine (AGENTS.md is there for it). Read the diff yourself, and make sure
  the tests pass.

By contributing you agree that your work is released under the project's license, the [GNU AGPL version 3](LICENSE).

decompdecomp continues [decomp.wtf](https://github.com/i12bp8/decomp.wtf), which is archived. Please don't open
issues there.
