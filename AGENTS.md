# AGENTS.md

How to work in this repository. Read README.md for what decompdecomp does from a user's point of view.

## What this is

decompdecomp: AI agents decompile games the user owns into C that compiles, with the game's original compiler, to
the exact same bytes. Python 3.12+, managed with uv. A continuation of decomp.wtf (Sam Wolfs, i12bp8/decomp.wtf,
archived at 0.10.6): `origin` is n-delic/decompdecomp, `upstream` the archived original.

Naming: the project is **decompdecomp** in everything users read. Internal names stay as decomp.wtf left them, so
existing installs and projects keep working: the `decomp` command, the `decompnet` package and data folder,
`.decomp.wtf.json` manifests, `.decomp-wtf/` state, the `decomp-wtf` git branch. Installs and updates
come from GitHub (`REPO` in app/updates.py and install.sh); the decomp.wtf domain isn't ours, so nothing may point
at it. `site/` is the website (decomp.ndelic.com, with automatic GitHub Pages publishing currently paused in this fork); `site/public/` also holds the installer, `install.sh`, which updates read
from GitHub. `main` is what users update from.

This fork defaults to `~/Decomp-RetroReversing` (checkouts: `~/Decomp-RetroReversing-dev`),
separate from upstream databases. Respect explicit `DECOMP_HOME` settings; do not auto-migrate old data.

## Commands

```bash
uv sync
uv run decomp                 # the development copy of the app, in its own window; projects in ~/Decomp-RetroReversing-dev
uv run pytest -q              # the test suite; many tests skip when a compiler or tool isn't installed
uv run pytest tests/test_forks.py -q   # one file
uv run ruff check src tests projects   # the linter (CI runs it and the tests)
```

Don't build the Rust gateway (`gateway/`) in a checkout or worktree unless the change touches it: the app uses the
downloaded copy, and parallel release builds of it starve the machine (docs/MCP_GATEWAY.md).

## Never disturb the user's own decompdecomp

The user usually has decompdecomp running (installed, port 6464, in its configured data home) with agents working in it.
Testing a change must not touch it:
- A checkout is "decompdecomp dev" (app/edition.py): its own home `~/Decomp-RetroReversing-dev`, port 6474, its own window class,
  no launcher. One instance is one per home, so it never replaces the installed app. Never point it at the live app's data home
  (`DECOMP_HOME`), or at upstream's `~/Decomp`. For experiments, give it a throwaway home: `DECOMP_HOME=<scratch dir> DECOMP_TRAY=0 uv run decomp`
  (`DECOMP_WINDOW=0`: no window).
- The agent CLIs the app lists (`agy models` above all) start the MCP servers in their own settings. The app finds
  them outside PATH too (`~/.local/bin`, `~/.opencode/bin`, ...), so a running experiment also needs a scratch `HOME`
  (with `DECOMP_WINDOW=0`); stub them in tests.
- Tests already run with a throwaway `DECOMP_HOME` and no window (tests/conftest.py). When the user's
  app runs from this checkout's `.venv`, run them in another environment so uv doesn't rebuild it underneath:
  `UV_PROJECT_ENVIRONMENT=<scratch venv> uv run pytest -q`.
- To try a change on a real project, work on a copy: `sqlite3 "file:$HOME/Decomp-RetroReversing/.decomp/decomp.db?mode=ro"
  ".backup <copy>/.decomp/decomp.db"`, copy the project folder, point `projects.root` in the copy at it. Read the
  live database only with `?mode=ro`.
- Don't restart, update or quit the user's app, and don't change its projects, without asking.

Compilers and tools (objdiff, dtk, wibo, Ghidra, m2c, decomp-permuter, period compilers) are pinned with checksums
in `src/decompnet/toolchains.toml` and installed by `src/decompnet/toolchains.py`, never taken from PATH. The one
exception is wine (`src/decompnet/wine.py`), which runs the compilers wibo can't (Visual C++ 2017) on Linux and macOS:
it's a whole Windows runtime that the user's system installs and keeps updated, with no build the app could pin for
every distribution and Mac, so it comes from `$DECOMPNET_WINE`, the PATH or the places wine.py lists, and only the
packs marked `wine = true` use it. The compilers it runs are pinned like the rest. The other exception is the kits
users bring (`src/decompnet/kits/`, a connector like `consoles/` and `agents/`): proprietary SDKs and the compilers
that only came in them (the Xbox XDK and its early Visual C++ builds), which nobody may publish, so nothing can pin
them. The user points Setup at their own copy; decompdecomp records the folder and reads it there, never copies or
downloads it, and checks its files against the SHA-256 of the builds it knows (`Kit.known`). A console takes a kit
by naming it (`Console.kits`); the user may give several copies of one kit (two XDKs, a Visual C++ 6.0 and a 2008), and a console picks the copy whose build is the game's; nothing outside `kits/` and that console names one (`tests/test_kits.py`). splat is a Python dependency in
`pyproject.toml`. Proprietary compilers are only installed after the user accepts that in Setup; don't add code that
fetches them silently.

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
- twins (in a game and across your games), other releases of a game (`release_key`, app/regional.py), the permuter,
  shared knowledge;
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
mode it can, does a new or changed console have each feature or a "—" with its ROADMAP item, and do the table and
the site's cards still say what's true? A gap nobody wrote down is a finding.

Improving a console, adding a console (copy `consoles/template/`, fill in the connector's parts, add a demo game,
pass `tests/test_consoles.py`, add a ROADMAP.md column) and adding a compiler to a console: CONTRIBUTING.md,
"Consoles", step by step.

## Agents

The helper agents the app starts work the same way. Each agent is a module in `src/decompnet/agents/`: `claude.py`
(Claude Code), `codex.py`, `antigravity.py` (Antigravity CLI), `opencode.py`, `cursor.py`, `kimi.py`, `zcode.py` (ZCode
CLI), `ohmy.py` (Oh My Pi), and `api.py` (the API helper: the app's own loop for any OpenAI-compatible API).
`agents/__init__.py` is the connector: the `Agent` class (every fact and part, one line each) and the list of agents.
What's the same for every agent (starting, restarting, waiting out usage limits, the records, ending their processes)
is in `app/agents.py`. The rest of the app asks `agents.get(kind)` and never compares agent names: an agent-specific
`if` outside `agents/` is a bug (`tests/test_agent_kinds.py` checks it, the GUI too: it gets facts like
`uses_endpoints` from the server).
Adding an agent (copy `agents/template.py`, fill in its parts, register it, pass `tests/test_agent_kinds.py`):
CONTRIBUTING.md, "Adding an agent".

The repair families that propose C changes from a byte difference are a library of their own, decomprepair, in its
own repository: RetroReversing/decomp-repair (public, AGPL-3.0, history kept). decompdecomp depends on a tagged archive
of it (`pyproject.toml`), so installing needs no git. The library never imports `decompnet`, and never compiles or
judges. A new repair that only reads C and code goes there: change it in decomp-repair, tag a release, then bump the tag
in `pyproject.toml` and `uv lock` here. A repair that needs the network or a project stays in `app/` and calls it.
`forks/csource.py`, `rom/symbols.py`, `arch/mips.py`, `m2cfix.py` and the MIPS evidence readers in `arch/`
(`mips_stack`, `mips_regions`, `mips_runtime`, the CP0/FPU leaves `mips_count` etc., since 0.2.0) are names for the
library's modules (`tests/test_decomprepair.py`); `seeds.intrinsic_leaf` decides which compilers get the leaves. The ~130 tests here that exercise its families with the app's compilers stay here.

The asset recognisers are a library of their own too, decompassets, in RetroReversing/decomp-assets (public,
AGPL-3.0, history kept), pinned by tag the same way: the N64 recognisers (textures, sound banks, models, animation,
fonts, resource directories), RNC, and the pixel/PCM/VAG readers every console shares. It uses only the standard
library and never imports `decompnet`; what it needs from the app comes in its `context` (the N64 console passes
`arch.mips_assets.read` as `read_code`). A change to how an asset is recognised or previewed goes there, released
and bumped like decomprepair. `rom/asset_formats.py`, `rom/asset_archives.py` and `consoles/n64/<asset module>.py`
are names for its modules (`tests/test_decompassets.py`).

The permuter and deep permuter (`app/permute.py`, `app/deep_permuter.py`, `app/permuter_worker.py`,
`app/deep_sweep.py`) follow the same rule. Adding or changing one of their C rewrites (a mutation, a search
strategy over source, which rewrites to try for a difference) goes in decomp-repair: make the change there on a
branch, open and merge its pull request, tag a release, then bump the tag link in `pyproject.toml`, `uv lock`, and
call the new code from the permuter here, in one pull request. What runs candidates stays in `app/`: compiling,
scoring, the judge, workers, budgets, progress and the database. A permuter change that is only about running them
needs no library release.
Since 0.3.0 the permuter's rewrites are there: `permuter.py`, `app/permuter_local.py`,
`app/permuter_initialization.py` and the generator `permuter_gen.py` with `permuter_capture.py`,
`permuter_dependencies.py` and `permuter_formatting.py` are names for the library's modules. `app/permute.py` runs
the library's `permuter_gen.py` by path, its helpers beside it. Don't add code to those alias files: change the
library. The worker's restart check (`permuter_worker.WATCHED`) and `claim_stalls._repair_rules` read a name from
the library when it lives there, so a moved module needs no new entry.
Since 0.4.0 `switch_constant_dispatch`, `initializer_clusters`, `callee_contracts`, `loop_storage_shapes`,
`repair_tokens` (in `app/`) and `consoles/ps1/gte.py` are aliases too. `app/switch_tables.py`,
`app/layout_frontiers.py` and `app/expression_recipes.py` import their rewrites from the library and keep only what
reads the project database (`read`, `saved`, storing recipes): split a module that way when only part of it is pure.

Shared pieces: `network.py` (claims, attempts, notes, shared header), `compiler.py` (sandboxed compiler runs),
`scoring.py` (capstone disassembly and diffs for every arch), `elfobj.py` (ELF objects), `classes.py` (C++ classes
from the vtables constructors store, MIPS and x86, named by a PC program's RTTI, `consoles/pc/rtti.py`: the `get_class`
tool and the class lines of `get_function`),
`m2cfix.py` (m2c's usual mistakes undone for MIPS GCC games, and its macros expanded for the permuter),
`structs.py` (struct layouts from the loads and stores through pointers, read by the processor's
`Arch.field_accesses`, MIPS: `arch/mips_fields.py`, x86: `arch/x86_fields.py`; the `guess_struct` tool and the brief's lines, `app/engine/layouts.py`),
`compilermap.py` (which compiler built each part of a MIPS game; the judge's survey is `engine.survey_compilers`;
`app/rangecheck.py` tests a suggested range on its neighbours before it applies), `app/tools.py` (the MCP
tools helpers get and their prompt; MCP is only the app's private wiring to the CLI helpers it starts; helpers reach it through the MCP gateway, `gateway/` (Rust) started by `app/mcp_gateway.py`, which answers every request within a deadline: docs/MCP_GATEWAY.md), `app/window.py`
+ `app/gtkwindow.py` + `app/macwindow.py` + `app/tray.py` (the window: Linux uses system GTK/WebKitGTK, macOS uses
Cocoa/WebKit through the app's Python; the page answers only a window with the secret in its user agent; Linux and
Windows have a tray icon; tests set `DECOMP_WINDOW=0` and
`DECOMP_TRAY=0`), `app/edition.py` (release or development copy), `app/saves.py` (a ROM project's work as plain files, and a
project restored from them), `app/readable.py` (polishing matched code), `app/knowledge.py` (what agents learn,
shared once two models agree), `app/drafts.py` + `app/permute.py` (background drafts and the permuter),
`structs.py` (struct layouts from loads and stores), `app/guide/*.md` and `consoles/<id>/guides/*.md` (know-how agents read through `guide(topic)`), `app/static/` (GUI: `index.html`, `app.css`, and `js/*.js`, plain scripts loaded in order; `js/base.js` says which is which).

## Compiler matching evidence

When a match reveals a reusable compiler behaviour, update `docs/COMPILER_MATCHING_LEARNINGS.md`.
Record the exact compiler/flags, source change, measured result and evidence level. Include failed
experiments that would otherwise be repeated. Keep game data private and distinguish controlled
regression tests from new matches. Add a short version to the appropriate compiler guide when useful.

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
- A change goes on a branch named `<type>/<short-name>`, the type one of `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `ci`, `build`, `chore`, `revert` (`fix/ps2-long-symbol-names`); keep this naming convention even while the automatic label workflow is paused. An issue you open (`gh issue create`) gets `--label` for its kind (`bug`, `enhancement`,
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
- A change that adds or changes a console's support, or how decompdecomp is installed or downloaded, also updates
  `site/public/index.html` (the console's card and picker entry, the install section) in the same pull request;
  reviewing a change checks it, like the ROADMAP table.

## Shared UI and UX patterns

- Saved/sidebar projects are not active work. Routine per-project maintenance must use
  `app/project_activity.py`: recently viewed projects, live managed agents, and explicitly started
  project jobs may run; idle projects must not open operational stores or start scans merely because
  they exist. Preserve explicit jobs across navigation. Scope new jobs with `project=slug` when they
  can start outside a project route. Lightweight room heartbeats may retain membership while bulk
  synchronization sleeps. Explicit donor searches may read idle projects without activating them.

- User-started project tasks should process all eligible work in one run, with visible Stop and durable progress. Do not impose a fixed per-run function batch that requires repeated clicks. Internal database paging and bounded per-function searches remain appropriate.

- Reuse the app's existing components before adding markup or CSS. Project tools and settings use
  `<details class="panel settings-disclosure">`, a `<summary class="panel-head"><h2>…</h2></summary>`
  and a `<div class="pad">` body. Include the full shell: `settings-disclosure` alone does not provide
  header spacing. Keep independent tools as sibling cards, collapsed by default; preserve open state
  during refreshes. Use `disclosure-context` for a short purpose or count beside the title.
- Data tables should sort by clicking their column headings. Use native buttons inside `th scope="col"`,
  visible direction indicators and `aria-sort`; toggle ascending/descending, sort numbers numerically,
  keep missing values last, and preserve the choice across refreshes of the same view. Default to the most
  useful ordering (compiler comparisons: Used by, highest first). Keep action-only columns unsortable and
  preserve each row's identity when sorting so its actions still affect the correct item.
- Within a panel, group status, counters and controls with spacing and subtle dividers rather than an
  extra bordered card inside the card. Reserve nested boxes for genuinely separate content.
- Keep standalone actions directly visible in the active card. Do not hide buttons such as Reset metrics
  inside a generic “More actions” disclosure. Disclosures are for optional settings, explanations and
  diagnostic details; use a labeled action button for a single secondary action. Use ⋮ menus only when
  grouping several related secondary actions avoids a crowded row.
- Avoid rows of secondary action buttons. Keep the main action visible and group related secondary actions
  in a vertical ⋮ overflow menu using the existing `agent-menu` / `agent-menu-items` pattern (also used by
  compiler profiles). Give the trigger a descriptive accessible label, show one action per line, retain
  disabled states, close on selection/outside click/Escape, and restore focus to the trigger. Keep actions
  such as Stop visible while work is running; do not hide essential progress or errors inside menus.
- Start with one sentence explaining the outcome and when the tool is useful. Put limits beside their
  labeled inputs, use a clear action verb on the primary button, and keep technical details in a secondary
  disclosure. Avoid generic “Finished” messages, repeated descriptions and unexplained internal terms.
- Use the shared dropdown styling for native single-choice `select` controls, shared field,
  button, status and progress styles, and theme variables. Do not introduce native-looking one-off menus,
  hard-coded theme colors or a separate card system. Check both themes and narrow layouts.
- Never "try once, fail if busy". Work that has to wait for a shared resource (a Git repository, a project lock,
  a compiler slot) queues for it: a user's click waits its turn in the queue with progress, what it waits for and
  Stop (`repository_operations.push` for repositories), and never fails with "another operation is using this".
  Background workers may skip a busy resource only when their work stays durably pending and retries on its own.
  Queues are visible in Background work (`work_monitor.py`), one row per distinct request.
- Every user action that can wait (including Stop, disconnect, remove, save and settings changes) must acknowledge intent immediately, before its first network request: show the action in progress, prevent duplicate/conflicting clicks, retain feedback across polling redraws, and show success or an actionable error. Never leave a click silently pending or discard an error; restore controls on failure. Test delayed responses, duplicate clicks, errors and stale refreshes.
- A click that starts work must show feedback immediately, disable duplicate starts, and offer Stop when
  cancellation is supported. Reuse `beginWork`, `followJob`, `showJobError` and `.workbar`; clean up in
  `finally`. Show real progress when totals exist, otherwise indeterminate progress. Keep errors actionable
  and visible. Distinguish the current run from saved totals and never show stale data from another project.
- User-started jobs must remain visible at their originating panel when the user navigates away
  and returns or reopens the window. Discover active work from server-owned job IDs and explicit
  task/project scope before loading potentially slow statistics; do not rely on browser variables.
  Reattach the existing progress/log/Stop controls, disable duplicate starts, and show activity in
  the panel heading even when collapsed. Reuse the same follow/completion/error path as a new job,
  guard against duplicate followers and late discovery/statistics responses, and never restart work
  merely to rediscover it. Test navigation, fresh-window attachment, completion, cancellation and errors.
- Routine background synchronization should use a compact header indicator with details on demand, without
  inserting banners that shift content. Reserve prominent progress banners for full rechecks or work that
  requires attention; use explicit task scope rather than guessing from progress text.
- Make summary metrics actionable when details exist: use the shared side drawer and retain function links.
  Verify collapsed/expanded, working, empty, success and error states for the UI touched. Existing examples:
  `machineDraftPanel`, `savedMatchRecoveryPanel` and project settings in `app/static/index.html`.

## Never

- Commit, download or publish game data (ROMs, discs, executables, extracted code or assets). Generated projects
  keep them out of git (`orig/`, `extract/`, `build/`).
- Let agents edit a project's build setup: `forks/project.py` (`EDITABLE`, `FORBIDDEN_PARTS`) limits them to
  source and header files on purpose.
- Weaken the judge: a match means byte-identical after linking (ROM mode) or objdiff 100% (fork mode).

## RetroReversing contribution workflow

Always submit decompdecomp changes on a feature or fix branch through a pull request; do not commit directly to main.

The user has asked that completed PRs be merged to main after inspection and required checks, until they
revoke this preference. The user updates their running app themselves: merging a PR does not authorize
pulling into its checkout, stopping, updating or restarting it. Leave the running app in
`/Volumes/Data/Github/decompdecomp-rr` and its home `/Volumes/Data/Decomps/decompdecomp` alone unless the
user explicitly requests deployment. This replaces the previous automatic-update authorization.

When the user explicitly requests deployment, use `git pull origin main` followed by
`DECOMP_HOME=/Volumes/Data/Decomps/decompdecomp /Volumes/Data/Github/decompdecomp-rr/.venv/bin/decomp`.
Use a clean checkout of main (a detached origin/main runtime checkout is acceptable when main is held by
another worktree), preserve all other branches and local changes, stop only this home's app gracefully,
launch it persistently and verify health/revision. Experiments must still use isolated homes and must
not mutate live projects.

When a change requires clients to update, build a fresh shareable macOS app after testing
with `bash packaging/macos/build.sh --app-only`. Deliver the .app folder directly in Downloads,
without a ZIP unless requested. For Apple Silicon, always deliver to the stable folder
`/Users/a/Downloads/decompdecomp_RetroReversing_macos_arm64/decompdecomp.app`, replacing
the previous exported app instead of creating version/commit-named folders. State the
architecture, version and source commit in the response. This export must not replace
or restart the installed Applications app unless explicitly requested.
