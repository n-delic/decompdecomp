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
from GitHub. `main` is what users update from.

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

Each console's code is in its own folder, `src/decompnet/consoles/<id>/`: `n64/`, `ps1/`, `ps2/`, `gc_wii/`
(GameCube and Wii), `pc/` (Windows games), `nds/` (Nintendo DS). Its `__init__.py` makes the console (its facts and
parts) and its docstring lists its files. `consoles/__init__.py` is the connector: the `Console` class (every fact and part, one line each) and the
list of consoles. The rest of the app asks `consoles.get(platform)` (in the engine: `self.console(proj)`) and never
compares console names: a console-specific `if` outside `consoles/` is a bug. What depends only on the processor
(relocations, disassembly, hand-written assembly, which readers exist for it) is in `arch/` (`mips.py`, `ppc.py`,
`x86.py`, `arm.py`). Consoles only public decomps cover are in `consoles/public.py`.

1. **ROM mode** (N64, PS1, PS2): `src/decompnet/rom/` splits the game (splat/spimdisasm, `split.py`; the console adds the
   code splat misses), and `rom/match.py` *links* each compiled candidate at the function's real address and
   compares bytes. Driven by `app/engine/` (one module per topic, `__init__.py` lists them; `common.py` holds what they share). Exported as a
   rebuildable tree by `rom/export*.py`.
2. **PROJECT mode** (GameCube/Wii/PC/DS started from scratch, and public decomps forked by repository address): a
   community-shaped project on disk with an `objdiff.json`; objdiff judges each unit. The console makes the project
   (`consoles/gc_wii/scratch.py`: dtk-template; `consoles/pc/scratch.py`: Ghidra finds functions, decompdecomp
   delinks them into COFF objects itself, from `consoles/pc/project_template/`; `consoles/nds/scratch.py`: ds-decomp,
   with a CodeWarrior version per source file that `try_compiler` switches, `Console.file_compiler`). `forks/project.py` detects and
   builds projects, `app/forkengine.py` drives them. Fork-mode helpers that read objects with pyelftools must
   tolerate COFF (PC, Xbox): treat an `ELFError` as "no verdict".

**One pipeline, every console.** A feature is built for every console it can reach, ROM mode and project mode
alike: claiming and briefs, drafts and autopilot, set-aside code and the agents' verdicts, joins and splits, the
permuter, twins, polishing, saves, Check again. Put the shared part where every console uses it (`app/`, `network.py`,
`forks/`, `rom/`, `arch/` for the processor), and only what really differs in the console (`consoles/<id>/`, through
the connector's parts). Where a mode or console can't have it yet, say why in the code and give it a ROADMAP.md item
and a "—" in its table: a gap is planned work, not the way things are. (Polishing was ROM-only for months, so a DS
game at 57% had no Polish to choose.) A new PROJECT console therefore implements the connector's `rename_symbol`
(polishing needs it), `split_source`, `join_functions` and, where its build allows, `set_file_compiler`, plus its
processor's piece rules if its analysis can cut functions: CONTRIBUTING.md, "Adding a console", step 4.

**Check every feature on the console you touch.** Adding or changing a console (or a feature) isn't done when its
own tests pass: run each feature the app already has on that console, for real, and make the broken ones work. The
list is ROADMAP.md's "What works where" table, one row per feature; today:
- starting from your own copy, the judge, machine drafts, autopilot, drafts that follow the header (`redraft`),
  C++ methods drafted as `Class::method`;
- twins (in a game and across your games), the permuter, shared knowledge;
- hand-written assembly and kernel stubs set aside, pieces joined to their function, the agents' "no C makes this"
  verdict, Check again, function-end reports (`report_boundary`);
- compiler ranges and suggestions, range checks, the compiler map and the judge's survey, flag search
  (`search_flags`);
- C++ classes (`get_class`), struct layouts (`guess_struct`), the game's own debug symbols in briefs;
- polishing, real names (`rename_symbol`), saves and Open a saved project, the saved README card, reimport, the
  rebuildable export, progress bars, rooms.

What made this reliable when the PSP was added: copying another console's column into the table is a guess, not a
check (the PSP's column was filled from the PS2's, then each row was run). Run each feature through the same engine
and network calls the app and agents use, on the console's demo game (`projects/demo-<id>`, built unstripped too) and,
where it matters, on a real game the user owns: in a scratch home (`DECOMP_HOME`/`HOME`, `DECOMP_WINDOW=0`), read
only, never copied into the repo. A break gets fixed in the shared code with a test that would have caught it
(a demo-based test that skips without its tools, like `tests/test_consoles.py`); what can't work yet gets its "—"
and a ROADMAP.md item. Say in the pull request which features you ran and how.

Reviewing a change (a pull request, an agent's branch): check the same list. Does the change reach every console and
mode it can, does a new or changed console have each feature or a "—" with its ROADMAP item, and does the table
still say what's true? A gap nobody wrote down is a finding.

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
- A window, dialog or panel that shows progress text or logs while doing work also shows a progress bar. Reuse
  `beginWork`, `followJob` and `showJobError` in `app/static/js/base.js` and the shared `.workbar` styling; start
  before the request and clean up in `finally`. Existing stateful import/restore flows keep their shared bar.
  See `docs/CONVENTIONS.md`, "Progress in dialogs and panels", for the pattern and tests.
- Errors shown to the user or agent are `ForkError` / `NetworkError` with a sentence they can act on.
- Every behavior change gets a test in `tests/`. Tests that need a real game or a compiler skip when it isn't there
  (CI has none of them); small homemade games/objects live in `projects/demo-*` and are built by their own scripts.
- `uv run ruff check src tests projects` passes (CI runs it). No formatter: don't run `ruff format`.
- A change goes on a branch named `<type>/<short-name>`, the type one of `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `ci`, `build`, `chore`, `revert` (`fix/ps2-long-symbol-names`); a pull request from any other branch fails `.github/workflows/labels.yml`,
  and the prefix labels it. An issue you open (`gh issue create`) gets `--label` for its kind (`bug`, `enhancement`,
  `documentation`, `question`) and for each console (`n64`, `psx`, `ps2`, `gc`, `wii`, `pc`, `ds`, `new-console`) and
  system (`linux`, `windows`) it's about: `gh label list` has them all.
- Releases bump the version in four places together: `pyproject.toml`, `src/decompnet/__init__.py`,
  `site/public/install.sh`, `uv.lock`; `packaging/release.sh X.Y.Z "what users get"` does that, commits, tags and
  pushes (a tag pushed before the bump fails release.yml's version check). Commit subjects read
  `decompdecomp X.Y.Z: <what users get, in one sentence>`, with a body explaining why. Pre-releases are
  `X.Y.Z-alpha` (`-beta`, `-rc`); `updates.version_key` orders them before X.Y.Z.
- A release is a `vX.Y.Z` tag on main. `.github/workflows/release.yml` builds the `.deb`, the `.rpm`, the AppImage
  (`packaging/linux/build.sh`, which also runs locally) and the Windows setup bundle (`packaging/windows/build.ps1`),
  then attaches them to the GitHub release; it can be run by hand
  for a pushed tag (`gh workflow run release -f tag=vX.Y.Z`). Linux package copies set `DECOMP_PACKAGE` and don't
  update themselves; the Windows copy updates its bundled packages in place.
- What's planned is in ROADMAP.md, with a table of which feature works on which platform: when a feature ships or
  reaches another platform, update the table and move its item to done.

## Never

- Commit, download or publish game data (ROMs, discs, executables, extracted code or assets). Generated projects
  keep them out of git (`orig/`, `extract/`, `build/`).
- Let agents edit a project's build setup: `forks/project.py` (`EDITABLE`, `FORBIDDEN_PARTS`) limits them to
  source and header files on purpose.
- Weaken the judge: a match means byte-identical after linking (ROM mode) or objdiff 100% (fork mode).
