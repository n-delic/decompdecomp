# How decompdecomp's code is written

These are the house rules. Read them once before your first change; they apply to people and agents alike.
[AGENTS.md](../AGENTS.md) is the short version agents read on every task, and [CONTRIBUTING.md](../CONTRIBUTING.md)
says how to send a change. Examples point at real code by file and name: search for the name to find it.

## 1. Where code goes

| You want to change | Look in |
| :-- | :-- |
| Anything about one console (its files, compilers, flags, guides, importer) | `consoles/<id>/` (`n64`, `ps2`, `gc_wii`, `pc`): its `__init__.py` lists its files |
| What every console has to say, and the list of consoles | `consoles/__init__.py`, the connector |
| What depends only on the processor (relocations, disassembly, hand-written assembly) | `arch/<processor>.py` |
| Importing a game from scratch, ROM mode (N64, PS2) | `rom/split.py` (splat), `app/engine/importing.py`; the console reads its file |
| Importing a game from scratch, PROJECT mode (GameCube, Wii, PC) | the console's `scratch.py`, opened by `app/forkengine.py` |
| Forking a public decomp | `forks/` (`project.py` detects and builds a project), `app/forkengine.py` |
| The judge | ROM mode: `rom/match.py` (MIPS). Fork mode: objdiff, run from `forks/project.py` |
| A tool agents call | the MCP definition and the `HANDLERS` table in `app/tools.py`, the API agents' schema in `agents/api.py` (`api_tool_schemas`), the work in `app/engine/<topic>.py` (ROM) or `app/forkengine.py` (forks) |
| What agents read about a function | `app/engine/briefs.py` |
| Claims, attempts, the judge's bookkeeping | `app/engine/judging.py`, under it `network.py` |
| Compilers, ranges, suggestions | `app/engine/compilers.py`; the compiler map `compilermap.py`; range checks `app/rangecheck.py` |
| C++ classes found in the code | `classes.py` |
| Struct layouts guessed from the code | `structs.py` (the types), `arch/mips_fields.py` (MIPS's reader), `app/engine/layouts.py` (`guess_struct`, the brief's lines) |
| Free matches | twins `app/twins.py` (also across your games), autopilot `app/engine/autopilot.py`, the permuter `app/permute.py` (and its generator `permuter_gen.py`, run in decomp-permuter's environment; `permuter.py` the simple rewrites without it) |
| Machine drafts | `seeds.py` (m2c, and C++ methods), drafted in the background by `app/drafts.py`; forks: `forks/drafts.py` |
| Polishing matched code (names, types, comments that still match) | `app/readable.py` |
| What agents learn, shared once two models agree | `app/knowledge.py` |
| Saving a ROM project as plain files, and restoring one | `app/saves.py` |
| Helper agents: starting and restarting them, their records | `app/agents.py`; their prompt: `WORK_PROMPT` in `app/tools.py` |
| One agent (its command line, reading what it prints, its models; the API helper's own loop) | `agents/<id>.py`: `claude`, `codex`, `antigravity`, `opencode`, `api` |
| What every agent has to say, and the list of agents | `agents/__init__.py`, the connector |
| Look-alikes (`find_similar`) | `similarity.py` |
| Decompiling together | `app/collab.py`, `app/engine/rooms.py`, `app/routes/rooms.py` |
| The GUI's API | `app/routes/<part>.py` (`app/routes/__init__.py` lists the parts); what they share `app/support.py` (jobs, settings) |
| The GUI | `app/static/`: `index.html`, `app.css`, and `js/` (nine plain scripts loaded in order, one per part of the GUI; `js/base.js` lists them; no build step). The notification centre is in `js/sidebar.js` |
| Know-how agents read | `app/guide/*.md` (shared), `consoles/<id>/guides/*.md` (one console's), served by `guide(topic)` |
| Compilers and tools themselves | `toolchains.toml` (pinned, with checksums), `toolchains.py` |
| The app's window, tray, updates | `app/window.py` + `app/gtkwindow.py`, `app/tray.py`, `app/updates.py`; dev or release: `app/edition.py`; the `decomp` command `app/cli.py`; the apps-menu entry `app/launcher.py`; what the first start removes from older versions `app/cleanup.py` |

A console's code stays in its folder and reaches the rest of the app only through the connector: the engine,
briefs, twins, the agents' tools and prompts, the export and the GUI ask `consoles.get(platform)`, never `if
platform == "ps2"`. A new console is a new folder (copy `consoles/template/`), a demo game in `projects/demo-*`
whose build writes `build/demo.json`, an entry in the list in `consoles/__init__.py`, a passing
`tests/test_consoles.py`, and a column in the table "What works where" in [ROADMAP.md](../ROADMAP.md).
CONTRIBUTING.md, "Consoles", has the steps, and the steps for adding a compiler to a console. Every new feature
adds its row or its ticks to that table.

Agents work the same way. One agent's code stays in its module in `agents/`, and the rest of the app (the helpers'
manager in `app/agents.py`, the routes, the GUI) asks `agents.get(kind)` for a fact or a part, never `if kind ==
"opencode"`. The GUI gets the facts it shows agents by from the server (`uses_endpoints`, `takes_effort`). A new
agent is a new module (copy `agents/template.py`), an entry in the list in `agents/__init__.py`, and a passing
`tests/test_agent_kinds.py`. CONTRIBUTING.md, "Adding an agent", has the steps.

A new feature that touches the engine keeps its logic in a module of its own and adds small hooks to the engine
(see `app/rangecheck.py` and where `self.rangechecks` is used). That keeps the engine's modules readable and makes
the feature easy to move.

## 2. Names

- The project is **decompdecomp** in everything users read. Internal names stay as decomp.wtf left them, because
  existing installs depend on them: the `decomp` command, the `decompnet` package, `~/Decomp`, `.decomp.wtf.json`,
  `.decomp-wtf/`, the `decomp-wtf` branch. The decomp.wtf domain isn't ours: nothing points at it.
- Python: `snake_case` for functions and variables, `CapWords` for classes, `UPPER_CASE` for module constants
  (`STALL_BUDGET`, `MAX_FLAG_VARIANTS`), a leading underscore for what's private to its module or class. The engine's
  parts are `<Topic>Mixin` classes.
- Names use the domain's words: `claim`, `attempt`, `target`, `twin`, `range`, `brief`, `judge`. Not the
  implementation's (`process_item`, `handle_data`).
- Tests are named as a sentence of behaviour: `test_an_install_from_main_updates_on_releases_only`.

- Named values, not bare ones: no single letters outside tiny loops, no bare hex or magic numbers in logic. Name a
  value once and use the name (`VTABLE_HEADER_BYTES = 8`, `CHECK_SECONDS = 20`). People and agents both read this
  code; a name says what a value means.

## 3. Comments and docstrings

The most distinctive rule of this codebase: comments and docstrings say **why**, in plain English, and often name
the game or the incident that caused the rule.

```python
def try_compiler(self, agent, slug, function, compiler, flags="", source=""):
    """A function's C (`source`, else its best attempt) built with another compiler and flags, judged like an
    attempt but recorded nowhere: to work out which compiler built a part of the game before suggesting it (on GTA
    San Andreas an agent tried compilers by suggesting them: 128 of its 149 suggestions were disproved)."""
```

- Don't restate the code. `i += 1  # add one` is noise; `# the delay slot runs before the call` is information.
- A module starts with a docstring that says what it's for and how it fits with the rest. Good examples:
  `compilermap.py`, `classes.py`, `app/twins.py`, `app/rangecheck.py`.
- Every public function longer than a few lines has a docstring.
- Cite an issue as `#22:` and a game by name when one caused the rule.
- A shortcut with a known limit gets a comment `shortcut: <the limit>, <when to upgrade>`.
- No credit lines for where an idea came from; the commit history is the record.

## 4. Text people and agents read

- **Errors** are `NetworkError` (ROM mode and shared code) or `ForkError` (fork mode) with a sentence the reader can
  act on: what's wrong, and what to do. `raise NetworkError("say which compiler built those ranges (compiler=...)")`
  (`suggest` in `app/engine/compilers.py`). Never a bare exception's text, never a stack trace in a message.
- **Tool results** are plain text written for an LLM to act on: what happened, then what to do next.
- **GUI text** is short and plain, without jargon the user didn't bring.
- **Docs** (README, GUIDE, the guides for agents) use simplified English: short sentences, one instruction per
  sentence, the same word for the same thing.

## 5. Errors and robustness

- Catch the exceptions you expect and can say something about.
- A broad `except Exception` is only for code that must never stand between the user (or an agent) and their work:
  a heuristic like the class recovery, a background check. Then it logs the trace and says why in its comment:

  ```python
  except Exception:  # never stands between an agent and the function
      logging.getLogger("decompdecomp").exception("the compiler map failed")
  ```

- Background jobs always end, with done or with an error that says why (`Jobs` in `app/support.py`).

## 6. External tools and safety

- Compilers and tools come only from the pinned toolchains (`toolchains.toml`), never from PATH.
- Proprietary compilers are installed only after the user accepts their licence in the app.
- Heavy tools run through `limits.capped(argv)` (a memory cap) and with a timeout.
- Agents may edit only source and header files: `EDITABLE` and `FORBIDDEN_PARTS` in `forks/project.py`.
- The judge is never weakened. A match is byte-identical after linking (ROM mode) or objdiff 100% (fork mode).
  decompdecomp never accepts code that "does the same thing".
- No game data anywhere in the repository, the tests, issues or screenshots: no ROMs, discs, executables, extracted
  code or assets. Generated projects keep them out of git (`orig/`, `extract/`, `build/`).

## 7. Dependencies

- No new dependency for a few lines of code. capstone, pyelftools, httpx and rabbitizer are already there.
- A new runtime dependency needs a reason in its commit message.
- Development tools (pytest, ruff) go in the `dev` dependency group, never in the runtime dependencies.
- No JavaScript build step: the GUI is plain files the app's window loads.

## 8. Concurrency

- The server is async (Starlette under the MCP server). Blocking work in a route goes through `await call(fn)`, which
  runs it in a thread (`call` in `app/server.py`). Never block inside an `async def`.
- Long work is a background job the GUI follows (`Jobs.start`), or a thread with its own state dictionary (autopilot,
  the compiler survey, range checks).
- Where two threads write the same state, a `threading.Lock` guards it (`RangeChecks` in `app/rangecheck.py`).

## 9. Tests

- Every behaviour change has a test in `tests/`.
- A test that needs a compiler, a tool or a real game skips when it's missing, and says what it needs:
  `pytest.mark.skipif(toolchains.installed("gcc-2.7.2-kmc") is None, reason="needs gcc-2.7.2-kmc")`. CI runs without
  any of them: the suite must pass there with those tests skipped.
- Homemade games and objects live in `projects/demo-*` and are built by their own scripts. Small hand-assembled
  machine code in a test is fine (`tests/test_classes.py`, `tests/test_compilermap.py`).
- Code that's hard to set up gets a small stand-in for what it uses (`FakeNet` in `tests/test_rangecheck.py`).
- Tests never reach the user's own app: `tests/conftest.py` sets a throwaway `DECOMP_HOME`, `DECOMP_WINDOW=0` and
  `DECOMP_TRAY=0`. Agent CLIs are always stubbed: listing Antigravity's models starts the MCP servers in its settings.

## 10. Never disturb the user's own decompdecomp

People working on decompdecomp usually have it running too, with agents working in it. A test or an experiment must
not touch it.

- A checkout is a separate app, "decompdecomp dev" (`app/edition.py`): its own home `~/Decomp-dev`, port 6474 and
  window class. A newer `decomp` that finds an older one running in the same home quits it and takes its place
  (`app/cli.py`), so never point a checkout at your real `~/Decomp`. For an experiment, use a throwaway home:
  `DECOMP_HOME=<scratch> DECOMP_TRAY=0 uv run decomp`, with a PATH without agent CLIs.
- If your app runs from the checkout's `.venv`, run the tests in another environment, so uv doesn't rebuild the one
  your app uses: `UV_PROJECT_ENVIRONMENT=<scratch venv> uv run pytest -q`.
- To try a change on a real project, use a copy: `sqlite3 "file:$HOME/Decomp/.decomp/decomp.db?mode=ro" ".backup
  <copy>/.decomp/decomp.db"`, copy the project's folder, and point `projects.root` in the copy at it. Read a live
  database only with `?mode=ro`.

## 11. Commits and releases

- Work on `main`, or on a branch for something big. `main` is what users update from.
- A commit's subject says what users (or contributors) get, in one sentence. The body says why.
- A release bumps the version in four places together: `pyproject.toml`, `src/decompnet/__init__.py`,
  `site/public/install.sh`, `uv.lock`. Its commit reads
  `decompdecomp X.Y.Z: <what users get>`. Pre-releases are `X.Y.Z-alpha` (then `-beta`, `-rc`).
- A release is a `vX.Y.Z` tag. `.github/workflows/release.yml` builds the `.deb`, the `.rpm` and the AppImage and
  attaches them.

## 12. Linting

- `uv run ruff check src tests projects` must pass. CI runs it on every push and pull request.
- The rules (`pyproject.toml`, `[tool.ruff.lint]`) are few on purpose: pyflakes (unused code, undefined names),
  import order, syntax errors, mutable default arguments, and closures over a loop variable. Each finds real
  problems.
- There's no formatter. The code has a hand-kept layout (aligned continuation lines, long one-line literals) that a
  formatter would undo, and a mass reformat would bury every real change in `git blame`. Don't run `ruff format`.
- There's no line-length rule. Keep lines near 120 characters; go a little over when breaking a line reads worse.
- To silence a finding, say why: `# noqa: B023  called within this pass of the loop`.
