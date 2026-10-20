# AGENTS.md

How to work in this repository. Read README.md for what decompdecomp does from a user's point of view.

## What this is

decompdecomp: AI agents decompile games the user owns into C that compiles, with the game's original compiler, to
the exact same bytes. Python 3.12+, managed with uv. A continuation of decomp.wtf (Sam Wolfs, i12bp8/decomp.wtf,
archived at 0.10.6): `origin` is n-delic/decompdecomp, `upstream` the archived original.

Naming: the project is **decompdecomp** in everything users read. Internal names stay as decomp.wtf left them, so
existing installs and projects keep working: the `decomp` command, the `decompnet` package and data folder,
`~/Decomp`, `.decomp.wtf.json` manifests, `.decomp-wtf/` state, the `decomp-wtf` git branch. Installs and updates
come from GitHub (`REPO` in app/updates.py and install.sh); the decomp.wtf domain isn't ours, so nothing may point
at it. `site/` is the website (decomp.ndelic.com, published to GitHub Pages by `.github/workflows/site.yml` on every
push to main that changes `site/public/`); `site/public/` also holds the installer, `install.sh`, which updates read
from GitHub. Work on `main`: it's what users update from.

## Commands

```bash
uv sync
uv run decomp                 # the development copy of the app, in its own window; projects in ~/Decomp-dev
uv run pytest -q              # the test suite; many tests skip when a compiler or tool isn't installed
uv run pytest tests/test_forks.py -q   # one file
uv run ruff check src tests projects   # the linter (CI runs it and the tests)
```

## Never disturb the user's own decompdecomp

The user usually has decompdecomp running (installed, port 6464, projects in `~/Decomp`) with agents working in it.
Testing a change must not touch it:
- A checkout is "decompdecomp dev" (app/edition.py): its own home `~/Decomp-dev`, port 6474, its own window class,
  no launcher. One instance is one per home, so it never replaces the installed app. Never point it at `~/Decomp`
  (`DECOMP_HOME`). For experiments, give it a throwaway home: `DECOMP_HOME=<scratch dir> DECOMP_TRAY=0 uv run decomp`
  (`DECOMP_WINDOW=0`: no window).
- The agent CLIs the app lists (`agy models` above all) start the MCP servers in their own settings. The app finds
  them outside PATH too (`~/.local/bin`, `~/.opencode/bin`, ...), so a running experiment also needs a scratch `HOME`
  (with `DECOMP_WINDOW=0`); stub them in tests.
- Tests already run with a throwaway `DECOMP_HOME` and no window (tests/conftest.py). When the user's
  app runs from this checkout's `.venv`, run them in another environment so uv doesn't rebuild it underneath:
  `UV_PROJECT_ENVIRONMENT=<scratch venv> uv run pytest -q`.
- To try a change on a real project, work on a copy: `sqlite3 "file:$HOME/Decomp/.decomp/decomp.db?mode=ro"
  ".backup <copy>/.decomp/decomp.db"`, copy the project folder, point `projects.root` in the copy at it. Read the
  live database only with `?mode=ro`.
- Don't restart, update or quit the user's app, and don't change its projects, without asking.

Compilers and tools (objdiff, dtk, wibo, Ghidra, m2c, decomp-permuter, period compilers) are pinned with checksums
in `src/decompnet/toolchains.toml` and installed by `src/decompnet/toolchains.py`, never taken from PATH. splat is a
Python dependency in `pyproject.toml`. Proprietary
compilers are only installed after the user accepts that in Setup; don't add code that fetches them silently.

## Consoles, and the two ways a game is decompiled

Each console's code is in its own folder, `src/decompnet/consoles/<id>/`: `n64/`, `ps2/`, `gc_wii/` (GameCube and
Wii), `pc/` (Windows games). Its `__init__.py` makes the console (its facts and parts) and its docstring lists its
files. `consoles/__init__.py` is the connector: the `Console` class (every fact and part, one line each) and the
list of consoles. The rest of the app asks `consoles.get(platform)` (in the engine: `self.console(proj)`) and never
compares console names: a console-specific `if` outside `consoles/` is a bug. What depends only on the processor
(relocations, disassembly, hand-written assembly, which readers exist for it) is in `arch/` (`mips.py`, `ppc.py`,
`x86.py`, `arm.py`). Consoles only public decomps cover are in `consoles/public.py`.

1. **ROM mode** (N64, PS2): `src/decompnet/rom/` splits the game (splat/spimdisasm, `split.py`; the console adds the
   code splat misses), and `rom/match.py` *links* each compiled candidate at the function's real address and
   compares bytes. Driven by `app/engine/` (one module per topic, `__init__.py` lists them; `common.py` holds what they share). Exported as a
   rebuildable tree by `rom/export*.py`.
2. **PROJECT mode** (GameCube/Wii/PC started from scratch, and public decomps forked by repository address): a
   community-shaped project on disk with an `objdiff.json`; objdiff judges each unit. The console makes the project
   (`consoles/gc_wii/scratch.py`: dtk-template; `consoles/pc/scratch.py`: Ghidra finds functions, decompdecomp
   delinks them into COFF objects itself, from `consoles/pc/project_template/`). `forks/project.py` detects and
   builds projects, `app/forkengine.py` drives them. Fork-mode helpers that read objects with pyelftools must
   tolerate COFF (PC, Xbox): treat an `ELFError` as "no verdict".

Improving a console, adding a console (copy `consoles/template/`, fill in the connector's parts, add a demo game,
pass `tests/test_consoles.py`, add a ROADMAP.md column) and adding a compiler to a console: CONTRIBUTING.md,
"Consoles", step by step.

## Agents

The helper agents the app starts work the same way. Each agent is a module in `src/decompnet/agents/`: `claude.py`
(Claude Code), `codex.py`, `antigravity.py` (Antigravity CLI), `opencode.py`, and `api.py` (the API helper: the
app's own loop for any OpenAI-compatible API). `agents/__init__.py` is the connector: the `Agent` class (every fact
and part, one line each) and the list of agents. What's the same for every agent (starting, restarting, waiting out
usage limits, the records, ending their processes) is in `app/agents.py`. The rest of the app asks
`agents.get(kind)` and never compares agent names: an agent-specific `if` outside `agents/` is a bug
(`tests/test_agent_kinds.py` checks it, the GUI too: it gets facts like `uses_endpoints` from the server). Adding an
agent (copy `agents/template.py`, fill in its parts, register it, pass `tests/test_agent_kinds.py`): CONTRIBUTING.md,
"Adding an agent".

Shared pieces: `network.py` (claims, attempts, notes, shared header), `compiler.py` (sandboxed compiler runs),
`scoring.py` (capstone disassembly and diffs for every arch), `elfobj.py` (ELF objects), `classes.py` (C++ classes
from the vtables constructors store, MIPS: the `get_class` tool and the class lines of `get_function`),
`structs.py` (struct layouts from the loads and stores through pointers, read by the processor's
`Arch.field_accesses`, MIPS: `arch/mips_fields.py`; the `guess_struct` tool and the brief's lines, `app/engine/layouts.py`),
`compilermap.py` (which compiler built each part of a MIPS game; the judge's survey is `engine.survey_compilers`;
`app/rangecheck.py` tests a suggested range on its neighbours before it applies), `app/tools.py` (the MCP
tools helpers get and their prompt; MCP is only the app's private wiring to the CLI helpers it starts), `app/window.py`
+ `app/gtkwindow.py` + `app/tray.py` (the window: GTK and WebKitGTK from the system, run by the system's python3; the
page answers only it, by the secret in its user agent; and the tray icon; tests set `DECOMP_WINDOW=0` and
`DECOMP_TRAY=0`), `app/edition.py` (release or development copy), `app/saves.py` (a ROM project's work as plain files, and a
project restored from them), `app/readable.py` (polishing matched code), `app/knowledge.py` (what agents learn,
shared once two models agree), `app/drafts.py` + `app/permute.py` (background drafts and the permuter),
`structs.py` (struct layouts from loads and stores), `app/guide/*.md` and `consoles/<id>/guides/*.md` (know-how agents read through `guide(topic)`), `app/static/` (GUI: `index.html`, `app.css`, and `js/*.js`, nine plain scripts loaded in order; `js/base.js` says which is which).

## Conventions

[docs/CONVENTIONS.md](docs/CONVENTIONS.md) has them in full (where code goes, names, comments, errors, tests,
linting). In short:

- Match the surrounding code: plain-English docstrings and comments that say *why*, often citing the issue or the
  case that motivated a rule ("#22: ..."). Describe a game by its kind, never by its title ("a PS2 game with 3,000
  functions"), in code, tests, commits and PRs alike; older comments that name one stay until touched. No new dependencies for a few lines; capstone, pyelftools, httpx are there.
- Heavy external tools run through `limits.capped(...)` (memory cap) with a timeout.
- Errors shown to the user or agent are `ForkError` / `NetworkError` with a sentence they can act on.
- Every behavior change gets a test in `tests/`. Tests that need a real game or a compiler skip when it isn't there
  (CI has none of them); small homemade games/objects live in `projects/demo-*` and are built by their own scripts.
- `uv run ruff check src tests projects` passes (CI runs it). No formatter: don't run `ruff format`.
- Releases bump the version in four places together: `pyproject.toml`, `src/decompnet/__init__.py`,
  `site/public/install.sh`, `uv.lock`. Commit subjects read
  `decompdecomp X.Y.Z: <what users get, in one sentence>`, with a body explaining why. Pre-releases are
  `X.Y.Z-alpha` (`-beta`, `-rc`); `updates.version_key` orders them before X.Y.Z.
- A release is a `vX.Y.Z` tag on main. `.github/workflows/release.yml` builds the `.deb`, the `.rpm` and the AppImage
  (`packaging/linux/build.sh`, which also runs locally) and attaches them to the GitHub release; it can be run by hand
  for a pushed tag (`gh workflow run release -f tag=vX.Y.Z`). Packaged copies set `DECOMP_PACKAGE` and don't update
  themselves.
- What's planned is in ROADMAP.md, with a table of which feature works on which platform: when a feature ships or
  reaches another platform, update the table and move its item to done.

## Never

- Commit, download or publish game data (ROMs, discs, executables, extracted code or assets). Generated projects
  keep them out of git (`orig/`, `extract/`, `build/`).
- Let agents edit a project's build setup: `forks/project.py` (`EDITABLE`, `FORBIDDEN_PARTS`) limits them to
  source and header files on purpose.
- Weaken the judge: a match means byte-identical after linking (ROM mode) or objdiff 100% (fork mode).
