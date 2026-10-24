# Contributing to decompdecomp

Thanks for helping. Bug reports, fixes, new platforms and better guides for agents are all welcome.

## The one rule: no game data

Never put any part of a game in an issue, a pull request, a test or a screenshot: no ROMs, disc images or
executables, and no code, assembly or assets taken from them. That includes the C your agents matched. It comes from
the game, and it belongs to you and the game's owner, not to this repository. Sam's request from decomp.wtf still
stands: only use decompdecomp on games you own, and respect the rules of the decomp projects you work with.

Before you attach a log (`~/Decomp/.decomp/logs/decomp.log`), read it, and cut out any code or assembly.

## Reporting a bug

[Open an issue](https://github.com/n-delic/decompdecomp/issues/new/choose) with the bug report form. It asks for
your version, your system, the console and compiler, what you did, what you expected and what happened, and the part
of the log around it, if it helps (see above). Your answers label the issue (its console and system).

The name of the game is fine to mention. Many bugs only show up on one game, and the name helps a lot.

## Working on the code

decompdecomp is Python 3.12+, managed with [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/n-delic/decompdecomp && cd decompdecomp
uv sync
uv run decomp                 # the development copy, in its own window (projects in ~/Decomp-dev, or $DECOMP_HOME)
uv run pytest -q              # the tests
uv run pytest tests/test_ps2.py -q   # one file
uv run ruff check src tests projects   # the linter
```

A checkout is a separate app, "decompdecomp dev": its own folder (`~/Decomp-dev`), port and window. It never
replaces the decompdecomp you have installed, and never touches its projects in `~/Decomp`. For an experiment, give
it a scratch folder: `DECOMP_HOME=/some/scratch/folder DECOMP_TRAY=0 uv run decomp`. The tests already use a folder
of their own.

Compilers and tools (objdiff, dtk, wibo, Ghidra, m2c, decomp-permuter and the period compilers) are pinned with
checksums in `src/decompnet/toolchains.toml`, and the app installs them in Setup. splat is a Python dependency
(`pyproject.toml`). Many tests skip when a compiler isn't installed, and that's fine. The homemade demo games in
`projects/demo-*` are built by their own scripts. The N64 demo (`projects/demo-rom`) also needs `ld.lld` and
`llvm-objcopy` on your PATH (Debian and Ubuntu: `apt install lld llvm`).

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

### Every console gets every feature

decompdecomp is one pipeline for every console: whatever helps agents on one console should work on all of them,
in ROM mode (N64, PS1, PS2) and project mode (GameCube, Wii, PC, DS, public decomps) alike. When you add a feature:

1. Build the shared part where every console uses it: `app/` and `network.py` for the engine, `rom/` and `forks/`
   for each mode, `arch/` for what depends on the processor.
2. Put only what really differs in `consoles/<id>/`, through a part of the connector (`consoles/__init__.py`) with a
   default that says plainly when a console can't do it yet.
3. A console or mode that can't have it yet: say why in the code, add an item to ROADMAP.md, and mark its column
   with "—" in the table. Don't leave it unsaid.

### Where a console's code is

Each console has a folder: `src/decompnet/consoles/<id>/`. Everything decompdecomp knows about that console is in
it. The rest of the app never asks "is this a PS2 game?". It asks the project's console, `consoles.get(platform)`,
through one file: the connector, `consoles/__init__.py`.

| Folder | Console | Mode | Its files |
| :-- | :-- | :-- | :-- |
| `consoles/n64/` | Nintendo 64 | ROM | `rom.py` the ROM file and its header · `code.py` code the game loads later, compressed code · `guides/` |
| `consoles/ps1/` | PlayStation | ROM | `exe.py` the game's program (PS-X EXE) and the disc's BOOT line; its discs and overlays through the PS2's readers · `guides/` |
| `consoles/ps2/` | PlayStation 2 | ROM | `elf.py` the game's program · `disc.py`, `chd.py` disc images · `overlays.py` code in the disc's other files · `syscalls.py` kernel calls · `symtab.py`, `mdebug.py`, `stabs.py`, `cheader.py`, `debuginfo.py` the game's own debug symbols · `guides/` |
| `consoles/gc_wii/` | GameCube, Wii | PROJECT | `scratch.py` a dtk-template project for the game · `guides/` |
| `consoles/pc/` | Windows PC | PROJECT | `scratch.py` Ghidra finds the functions, decompdecomp cuts them out of the .exe · `project_template/` · `guides/` |
| `consoles/nds/` | Nintendo DS | PROJECT | `scratch.py` a ds-decomp project for the ROM, a CodeWarrior version per source file · `project_template/` · `guides/` |

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
4. When a feature reaches the console, tick its column in ROADMAP.md's table. A feature you build for one console
   should reach the others too (see "Every console gets every feature" above).

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
   | `file_compiler(fp, source)`, `set_file_compiler(fp, source, compiler)` | PROJECT: a compiler per source file, when the project's build allows it: then `try_compiler` switches it (DS). |
   | `asm_reason(code)` | Why a function can't come from a compiler. The processor's check, with this console's facts. |
   | `up_front_guide(compiler)`, `m2c_target(compiler)` | The guide for a compiler's code; m2c's target for it. |

   **What every console's agents get.** ROM mode has these in `rom/` and the engine already. A PROJECT console
   implements them itself, because only it knows where its project keeps names and file boundaries. A part the
   console can't do yet keeps its default ("can't yet"), and it gets a ROADMAP.md item and a "—" in the table:

   | Part | What needs it |
   | :-- | :-- |
   | `rename_symbol(fp, version, old, new)` | Polishing and `rename_symbol`: real names in the project's symbol list (dtk's and ds-decomp's `symbols.txt`). Without it, polished code keeps addresses for names. |
   | `split_source(fp, version, source, at, new)`, `splits_sources` | `split_file`: a source file divided where a new one starts (a library's code, a second compiler). |
   | `join_functions(fp, first, last, keep)` | `join_functions`: a function the analysis cut in pieces made whole, once agents agree. |
   | `file_compiler`, `set_file_compiler` | `try_compiler`, when its build lets each source file have its own compiler. |
   | its processor's `piece` and `jumps` (`arch/arm.py`) | Pieces found and joined without agents, in `on_start` (see `consoles/nds/recover.py`), when a tool finds its functions (ds-decomp, Ghidra) and can cut them. |

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

## Agents

### Where an agent's code is

Each agent the app can start is one module: `src/decompnet/agents/<id>.py`. Everything decompdecomp knows about that
agent is in it: how a session starts, how to read what it prints, its models, where its settings are. The rest of
the app never asks "is this opencode?". It asks the helper's agent, `agents.get(kind)`, through one file: the
connector, `agents/__init__.py`.

| Module | Agent | Its parts |
| :-- | :-- | :-- |
| `agents/claude.py` | Claude Code | `claude -p` with the app's MCP server as its only one; its model aliases and versions |
| `agents/codex.py` | Codex CLI | `codex exec --json`, the user's own MCP servers switched off for the run |
| `agents/antigravity.py` | Antigravity CLI | `agy` with stream-json in and out; its models from `agy models`; once Gemini CLI |
| `agents/opencode.py` | opencode | `opencode run`, its own server per helper (opencode 2); its models from `opencode models` |
| `agents/api.py` | API helper | No program: the app's own loop for any OpenAI-compatible API, and the saved endpoints' presets |

Around them:

| Where | What |
| :-- | :-- |
| `agents/__init__.py` | The connector: the `Agent` class (every fact and part, one line each) and the list of agents. |
| `agents/template.py` | The module to copy for a new agent. |
| `app/agents.py` | What's the same for every agent: starting and restarting helpers, waiting out usage limits, their records, ending everything they started when the app quits. |
| `app/tools.py` | The tools every agent gets, and its prompt (`WORK_PROMPT`). |

An agent is one of two sorts:

- **A program** the user installs and signs in to (Claude Code, Codex, Antigravity CLI, opencode). The app runs it
  headless, a session at a time, in a private folder. It connects only to the app's own MCP server, with a token of
  its own, and may use only decompdecomp's tools. The prompt goes in on stdin; what it prints is read into the feed.
- **A loop in the app** (the API helper). It talks to a model's API itself and calls the tools directly.

### Improving an agent

1. Open `agents/<id>.py`. Its docstring says how a session runs and what the agent prints.
2. Change the part you need there. Something every agent needs (how long a helper waits after a failure) goes in
   `app/agents.py`.
3. Run its tests: `uv run pytest tests/test_agents.py tests/test_agent_kinds.py -q`. Never run the real program in a
   test: it starts the MCP servers in the user's own settings. Use a recorded line of its output instead.

### Adding an agent

1. **Copy the template.** `cp src/decompnet/agents/template.py src/decompnet/agents/<id>.py`. Rename the class and
   the object at the end.
2. **Pick its sort.** A program: fill in `command` and the session's parts. A loop in the app: leave `command`
   empty and fill in `run` (see `agents/api.py`).
3. **Fill in its facts.** Each is one line in the `Agent` class:

   | Fact | What it is |
   | :-- | :-- |
   | `id`, `aliases` | What helpers' records store as their kind. Keep it once helpers exist; old ids go in `aliases`. |
   | `name`, `vendor` | What the app shows: its name, and who makes it (or whose models it runs). |
   | `command` | Its program, looked for on PATH. Empty for a loop in the app. |
   | `install`, `signin`, `docs` | What "Install it for me" runs, how to sign in (shown when it isn't), its documentation. |
   | `family` | Who made its models, when it runs only one maker's ("gpt"). A function one model gave up on goes to another family first. |
   | `models_args` | The arguments that list the models the user's sign-in offers (`("models",)`), read by `read_models`. |
   | `tool_meta` | What it reads on each MCP tool's definition (Antigravity: load the tools up front). |
   | `recommended`, `uses_endpoints`, `takes_effort` | For the launcher: list it first; take its model and key from the saved API endpoints; offer a reasoning effort. |

4. **Fill in the parts it needs.** The rest keep their default, which does nothing or says it can't:

   | Part | What it does |
   | :-- | :-- |
   | `session_command(exe, workdir, mcp_url, token, model)` | One session's command line, and what it adds to the environment. |
   | `stdin(prompt)` | What goes in on stdin: the prompt, as the agent reads it. |
   | `parse(event, state)` | The feed items in one JSON event it prints: text, thinking, tool calls, errors, what the session spent. |
   | `clean_stderr(line)` | A line of its stderr worth showing, or "" for noise. |
   | `fatal(error)` | Whether an error means every try would fail the same way: the helper pauses instead. |
   | `models(path, run)`, `read_models(lines)` | The models to offer in the launcher. |
   | `config_model()` | The default model in the agent's own settings. |
   | `version(path)` | Its version (`--version` by default). |
   | `label(name, options, used)` | What the GUI calls a helper of it. |
   | `run(manager, session, agent)` | A loop in the app: one session's work. |

5. **Keep the user safe.** A session connects only to the app's MCP server, with the token it was given, and to
   none of the user's own MCP servers. It may use only decompdecomp's tools: no shell, no file edits, no web. The
   token never goes on the command line, where every process can read it, and the prompt goes in on stdin.
6. **Register it** in the list at the end of `agents/__init__.py` (`_registry`). The Agents page lists the agents in
   that order.
7. **Pass the tests**: `uv run pytest tests/test_agent_kinds.py -q`. It checks the facts, the ids, the command line
   (no token on it) and that no code outside `agents/` asks an agent's name. Add a test of its own to
   `tests/test_agents.py`: a recorded line of its output, and the feed items it gives.
8. **Say it**: add the agent to README.md's list of agents and to docs/GUIDE.md's table.

## Pull requests

- Work on a branch named `<type>/<short-name>`, the type one of `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `ci`, `build`, `chore`, `revert` (`fix/ps2-long-symbol-names`). CI fails a pull request from any other branch, and labels it by the prefix.
- A change to a console or a feature says which of the app's features you ran on it and how (AGENTS.md, "Check
  every feature on the console you touch"); reviewers check the same list.
- Keep each one to one change, and say in the description what it fixes or adds, and how you tested it.
- Commit messages describe what users get, in one sentence, with a body explaining why.
- Don't bump the version. Releases are made separately.
- Pull requests written with an AI agent are fine (AGENTS.md is there for it). Read the diff yourself, and make sure
  the tests pass.

By contributing you agree that your work is released under the project's license, the [GNU AGPL version 3](LICENSE).

decompdecomp continues [decomp.wtf](https://github.com/i12bp8/decomp.wtf), which is archived. Please don't open
issues there.
