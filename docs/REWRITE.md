# The rewrite

What the rewrite on the `rewrite` branch fixes, and why. Each item comes from using the app for real (two games with
helper agents at once, October 2026). The rewrite may break existing installs and projects: the README already
says a rewrite is coming. Delete this file when the branch merges.

Status per item: **open** (agreed, not started), **ask** (needs the owner's answer), **done**.

## The principle: plug and play

decompdecomp is powerful, but whoever opens it should understand every screen without knowing how matching
decompilation works. Explain like they're five: say what a thing does for them and what to do next, in plain words.
People with technical knowledge (the owner included) can open the details and dabble: every advanced control is
there, one step further in, never in the way. If the owner can't tell what a button does, it's wrong.

Known offender: the **Compiler** panel of the overview. "Survey again", "Check applied ranges", suggestions, range
checks and auto-adjust sit side by side, and nobody (the owner and the agent who built them included) can say what
each one does or when to press it. The user should see one answer ("This game was built with GCC 2.9; parts of it
with another compiler: here they are") and the app should do the checking itself.

## 0. Rules for the rewritten code

Everything in [CONVENTIONS.md](CONVENTIONS.md) still holds, plus:

- **Named values, not bare ones.** No single-letter variables outside tiny loops, and no bare hex or magic numbers
  in logic. Name them once and use the name: `JAL_OPCODE = 0x0C000000`, `VTABLE_HEADER_BYTES = 8`,
  `SWITCH_COST = 8`. People and agents both read this code; a name says what a value means.
- Breaking changes are allowed. No compatibility shims for the old app's data unless we decide one is worth it.
- **Except the owner's projects.** The games in `~/Decomp` (GTA San Andreas, Ridge Racer V, Mario Kart 64, king)
  keep being worked on with the release while the rewrite happens, and must carry over into the rewritten app with
  all their progress: through the re-import of §7, or a one-time conversion of the old database. Tested on a copy
  first; the originals are never written by development code.

## 1. A standalone app, and only that

**Problem.** The app keeps running after force-quitting it, and starts again by itself.

**Why it happens** (found on the owner's machine):
- Every MCP client that has decompdecomp registered starts the bridge, `decomp mcp`. The bridge calls
  `ensure_app()` (`app/bridge.py:42`), and that starts the whole app in the background when it isn't running. So any
  open Claude Code or opencode session starts decompdecomp again. Two Claude Code sessions each had a bridge running.
- The window is a Chromium `--app` window (Brave here) on `http://127.0.0.1:6464`. Closing it leaves the server
  running when helpers or jobs work (`app/window.py`). Anyone with the address and secret can open it in a browser.
- An `opencode serve --service` process outlived its parent (its parent is now PID 1). Check whether the app
  started it. If so, quitting the app must end it.

**Goal.** One standalone desktop app. No browser tab, no browser access. Quitting it ends everything it started:
server, helpers, tools. Nothing starts it but the user.

**Plan (open).**
- **No outside agents, no public MCP.** Only helpers the app starts work on projects. Remove the `decomp mcp`
  bridge (`app/bridge.py`, `ensure_app`), `decomp connect` and its writes into the user's agent configs
  (`app/connect.py`), and the GUI's "connect your agent" parts. The registration has done real harm: opencode merged
  the user's `decomp` server into helper runs, and every helper worked as the user's chat agent on one claim
  (`agents.py:895`).
- **MCP stays as internal plumbing only.** The CLI agents the app runs (Claude Code, Codex, opencode, Antigravity)
  take custom tools only through MCP. Each helper gets the app's endpoint on 127.0.0.1 with its own token, set up
  by the app for that run. The user never sees or configures it. API helpers keep calling the tools directly.
- The first run of the new version removes every registration of the old bridge, and lists what it removed, so
  nothing old can start the app:
  - What `decomp connect` added (Linux and macOS: `app/connect.py`; Windows: `decomp-connect.ps1` from
    `install.ps1`, which registers the WSL bridge on the Windows side):
    - Claude Code: `claude mcp remove -s user decomp` (the entry lives in `~/.claude.json`).
    - Codex: `codex mcp remove decomp` (`~/.codex/config.toml`, `[mcp_servers.decomp]`).
    - Antigravity CLI: `agy mcp remove decomp`.
    - opencode: the `mcp.decomp` key in `~/.config/opencode/opencode.json`.
  - What users added by hand from the README, in any MCP client that keeps a config file: Gemini CLI, Claude
    Desktop, Cursor, VS Code, Windsurf, Zed, Cline and the like. An entry counts only when its command runs
    `decomp mcp` (or the WSL bridge); anything else in those files stays as it is.
  - On Windows the cleanup runs on the Windows side too, where the WSL bridge was registered.
- Also goes: `decomp connect` in `install.sh` and `install.ps1`, and the "connect your agent" text in the README,
  docs/GUIDE.md and the website.
- Window: a native window (pywebview), not a browser. The UI stays HTML, served only to that window.
- Quit means quit: stop helpers (their whole process group), jobs, and the server.
- Remove the `--service` mode (`app/service.py`) and the browser fallback.
- **Packages: .deb, .rpm and AppImage.** The .rpm is built from the same tree as the .deb (`packaging/linux/build.sh`,
  release.yml), installs to `/opt/decompdecomp` and depends on Fedora's names for the window's packages
  (`python3-gobject`, `webkit2gtk4.1`); the .deb on Debian's (`python3-gi`, `gir1.2-webkit2-4.1`).

### `decomp` is the app, and a development copy never touches the release

- `decomp` (from the installer, the .deb or the AppImage) opens the standalone window. That's the only way to run
  the app; there are no other modes to remember.
- **Dev and release are separate apps.** A copy run from a git checkout (`uv run decomp`) is "decompdecomp dev":
  - its own home, `~/Decomp-dev` (its own database, projects and settings), unless `DECOMP_HOME` says otherwise;
  - its own port and window class, its own name in the window title, tray and notifications ("decompdecomp dev"),
    and a different icon colour;
  - no launcher in the app menu.
  - Release and dev never quit or replace each other: "one instance" means one per home. Today a newer copy
    finding an older one running quits it (`app/cli.py`, `replace_older`): that's how a test run once stopped the
    owner's app.
- One install at a time: the installer, the .deb and the AppImage each find and remove an older install of another
  kind (the old uv tool `decompnet`, `/opt/decompdecomp`, launchers named `decomp-wtf.desktop`), so the app menu
  shows one decompdecomp.

### Windows as the computer the app runs on: removed

Removed for this release: `site/public/install.ps1`, `src/decompnet/hostenv.py`, the WSL distribution set-up, the
Edge window through WSL, `tests/test_wsl.py`, and every branch that exists only for running on Windows.

Why:
- It was labelled experimental, and nobody tests it: the owner uses Linux, and CI runs none of it.
- Most of it served what the rewrite removes anyway: registering the MCP bridge with agents on the Windows side,
  and opening a browser window from WSL.
- It's a lot of code (hostenv.py, the 319-line installer, branches in 32 files) that every change has to work
  around.
- Windows 11 users still have a way: WSL shows Linux apps' windows (WSLg), so installing the Linux version inside
  WSL should work, unsupported. The README says so.

**After the rewrite: research how to support Windows well.** Many users will come from Windows, so the removal is
for this release only. Once the rewrite lands, an agent researches the best way back: native Windows (the
compilers that only run on Linux, sandboxing, a WebView2 window) against WSL with WSLg, against something else,
measured on the rewritten app, with what each costs to build and to test.

**Not removed: Windows PC games.** Decompiling a Windows game's `.exe` (the `win32` platform, MSVC through wibo,
`forks/scratch_pe.py`) is a console like any other, and it runs on Linux. Only Windows as the host goes.

The README, docs/GUIDE.md, ROADMAP.md's platform table and the website say Linux, with macOS and Windows "not
supported in this version".

## 2. UI/UX overhaul

**Problem.** A project has too many tabs (Overview, Functions, Agents, Activity, Team, Source, Settings), and some
things exist twice.

- **Agents + Activity → one view.** What each agent does and what it said belong together.
- **Team → the header.** The header already has "Decompile together". Drop the tab.
- **Start agents** is both in the header and on the Agents view. Keep one.
- **Source → an Export button** in the header.
- **"Next in line"** (Settings) is the priority list: functions with ★ that agents take first. Nobody could tell
  that from its name. Rename it or fold it into the Functions view (star a function there).
- **Keep the Overview map.** It's the best part: you see the game fill in.
- A full redesign pass for the new structure: layout, navigation, tokens, light and dark mode, in the native
  window. It comes after the agents work (§4) and the notification centre (§3), so it's designed around how they
  really behave. More UI issues the owner finds go here.
- **Report a problem**, reachable from every page: opens a new GitHub issue in the user's browser with the
  version, the platform and a short template, and copies the bug report (version, error, log) to paste in.
- **Save and restore** (§7) and **make readable** (§10) get their buttons in the new structure.

**Design (done).** The app keeps its design system ("Marked listing", site/design/DESIGN.md: the highlighter means
"being worked on", green only "matched", Archivo and Plex Mono, light and dark). It's a tool used every day (R1
Utility in the work views): what changed is the structure, so that every screen answers one question.

- **The sidebar, on every page:** New project (open a game file, **open a saved project**, continue a community
  decomp, join a team's room, the demo), your projects (each with its colour), Agents & models, Compilers & tools,
  Settings, **Report a problem**, and the **notification bell** beside the logo (§3).
- **A project has four views: Overview · Functions · Agents · Settings.**
- **The header does the project's actions:** "N agents working" (to Agents), **Decompile together** (or the room's
  pill once it's shared), **Export**, **Start agents** (the one place agents start from), the project folder.

Where every old feature went:

| Was | Now |
| :-- | :-- |
| Overview | Overview: the map first, the numbers, what to do next, one line for the compiler ("Built with KMC GCC 2.7.2; 1 part with another compiler. Details"), autopilot, recent matches, who matched what |
| Functions | Functions, with a ☆ column: star a function and agents take it before the others; a "★ First" filter lists the starred ones |
| Agents (launcher, helpers, instructions, what agents learned, suggestions) and Activity | Agents: your helpers on the left, what happens on the right ("What's happening"; a helper's "What it said and did" takes its place, "Back to everything" returns). Above them "Waiting for you" (where a function ends, agents' suggestions); below them the instructions and what agents learned |
| Start agents on the Agents view, and on the overview's next step | the header's Start agents only; the next step's button opens it. It also asks what the helpers do: decompile, or make matched code readable (§10) |
| Team | the header: Decompile together, or the room's pill, opens the room in a side panel (every option: tunnel or your own address, who can join, approving members, the invite link, stop sharing; a guest's view) |
| Source | the header: Export opens the export in a side panel (a fork: rebuild and compare, measure again) |
| Next in line (the ★ list) | ☆ in Functions and in a function's panel ("Do this first"); the "★ First" filter |
| Settings: the Compiler panel (Survey again, Check applied ranges, suggestions, range checks, auto-adjust) | Settings → Compiler: one answer ("This game was built with KMC GCC 2.7.2. One part of it with another compiler: below"), the parts with a plain state each (checked, being checked, waiting, doubtful) and Remove, the compiler changes waiting for you (Use it / Not now), "Apply proven changes by themselves". One click in: **How the app checks** (the survey, the checks, Check again now) and **Change the compiler yourself** (compiler, flags, ranges) |
| The compiler survey and the applied ranges' checks, run by hand | run by the app: `/api/compilermap` starts the survey when the game uses more than one compiler and the last one is an hour old, and looks at each applied part that hasn't been looked at |
| The map's compiler note with its buttons | the colours and one sentence, with a link to the answer |
| Settings: About this project, Remove project | the same; and **Save your progress** (§7: it saves itself every few minutes; save now, or to a folder you pick) |
| Per-project toasts (matches, autopilot, links, paused helpers) | the notification centre (§3) |

Accessibility: the views are links (`aria-current`), every function named on a page is a button, the side panels
take the keyboard and give it back where it was, Escape closes the top one, focus is the tokens' focus colour (olive
ink on paper, the highlighter on dark), text that used the border colour now uses the tertiary text colour (AA), and
toasts are paper with an ink hairline in both themes (an error's white text on the dark theme's light red was
unreadable). Checked by driving a throwaway copy in headless Chromium, every view in light and dark, and at 820 px.

## 3. One notification centre for the whole app

**Problem.** With two projects running agents, the app stalls. Opening the other project freezes it for a moment,
then floods it with notifications.

**Likely why.** Notifications are per project: the view of a project keeps its own feed and replays the backlog
when you open it. To confirm in the rewrite.

**Goal.** One app-wide feed. Each entry shows the project (colour-coded), the agent and what happened ("Ridge
Racer V · gemini-3.8-flash-2 matched func_0012A0"). Bursts collapse into one line ("+12 more matches"). Opening a
project never replays anything.

**Done.**
- `/api/events?after=N`: what happened after event N in every project, notable kinds only (matches, links, the
  autopilot, the compiler, reopened functions, what agents agree on, rooms, imports, **paused and stopped helpers**:
  the manager records those now). A range of the events' primary key, however long the history. Without `after` it
  only says where the feed is: the window starts there, so what happened before is never news.
  `/api/events?recent=1`: the latest, for the panel.
- The window asks every 2 seconds, whichever page is open. News of one kind from one project within 30 seconds is
  one line that counts up ("+12 more matches"); a burst's toast updates in place, at most three toasts show, a
  paused helper's line is red. Each line: the project's colour and name, the time, the agent and what happened; a
  function's name opens it.
- The bell (sidebar, and the narrow window's bar) shows how many new lines wait; its panel lists the history, newest
  first, bursts collapsed, "Which ones" for a burst's functions.
- Each project has a colour (six, in the order the projects were made, ordered so neighbours differ most, never the
  highlighter's yellow or the green of "matched"), the same in the sidebar and in every notification, always next to
  the name.
- A project's own poll toasts nothing: it redraws the project and flashes its fresh matches on the map.

## 4. Agents, redone

**Problems.**
- **No auto-restart.** Helpers stop ("agent stopped, see the message") and stay stopped. The message is in
  Activity, which nobody looks at.
- **Usage limits** stop a helper. It should wait and carry on by itself, with no user action.
- **Who is that?** After a restart, the Agents view shows names like `opencode-20` with only "agents running".
  Today's cause: helper sessions live only in the app's memory, but claims are in the database. A claim left by a
  helper from before the restart shows up as a "connected agent" with no stop button. Numbers keep climbing because
  the old names are still taken.

**Goal.**
- Every helper has one clear state: working (on which function), waiting (why, and until when), stopped (by whom,
  and the agent's last message), or failed (the error, readable).
- Auto-restart: a helper that stops or crashes starts again. On a usage limit, it waits until the limit resets,
  then starts again.
- The agent's own last words are visible where you see the agent.
- Helpers survive an app restart as records, not as ghosts. Their claims are released when their process is gone.
- Only helpers the app starts exist, so every agent on screen has a stop button. No "connected agent" rows.
- **Efficiency.** Measured on a copy of the owner's database and the app's logs (October 2026):

  | Helper | Game | Matches | Compiles per match | Matched at the first compile |
  | :-- | :-- | --: | --: | --: |
  | Claude Code | GTA San Andreas | 375 | 4.1 | 70% |
  | Claude Sonnet | Mario Kart 64 | 341 | 2.8 | 75% |
  | Gemini 3.8 Flash | Ridge Racer V | 267 | 7.1 | 44% |
  | Muse Spark | Ridge Racer V | 236 | 6.0 | 52% |
  | DeepSeek Flash (API) | Ridge Racer V | 108 | 8.8 | 42% |

  - Autopilot made 41% of all 3,200 matches, with no tokens.
  - Cost per match: about $0.15 (GTA San Andreas), $0.04 (Mario Kart 64). Ridge Racer V cost $4 in all but 240K
    tokens per match, likely the API helper resending its whole conversation.
  - 59-91% of claim time goes to functions that end released. Only 5.5% of released functions ever match later.
  - The chance that a compile matches falls fast: for functions up to 20 instructions 43%, 29%, 17%, 11% for the
    first four compiles; over 50 instructions it's 7% from the start.
  - Regions differ hugely: on GTA San Andreas small functions match 19-28% of the time in most of the game but 87%
    in one region; on Mario Kart 64 one region 19%, the rest 71-85%. 63% of give-up notes blame the compiler or
    flags.
  - 549 open functions are within 6% of matching. The permuter has touched only 52 functions (25 matched).
  - No helper has tried a GTA San Andreas function over 100 instructions: most of the game's code.
  - The database records no spend per helper or model, so models can't be compared on cost yet.

  Fixes, ranked:
  1. *(done, §9.2)* Near misses go to the permuter, which runs one worker per spare core (`app/permute.py`: one
     thread today).
  2. Pick functions by the expected chance of a match: their region's match rate times their size. A region that
     keeps failing goes to the compiler survey, not to more agents.
  3. A compile budget by size (about 3 for small functions; more only while the score keeps falling). Fix the guide
     that says "~20 compiles" while the prompt says 4.
  4. A released function goes next to a different model family, or waits until the shared header or compiler range
     changes, instead of coming back in 15 minutes.
  5. The shared header comes with the claim (once per session, then only what changed): one tool call less per
     claim.
  6. Longer sessions: 6-10 claims instead of 2, so each start's system prompt, tools and guides are paid less often.
     Measure cost per match before and after.
  7. The API helper folds a finished function's messages into one line and trims only there, so prompt caching
     keeps working.
  8. Record spend, tokens, turns and tool calls per helper session and model in the database.
  9. The brief shows matched callees' prototypes and the struct types of the data a function uses.
  10. Each project registers only its own tools (ROM projects get no fork-only tools).
  11. Source already compiled for a function answers "identical to attempt N" without compiling (about 3% of
      compiles).
  12. Bug: `read_data` (`engine/briefs.py`) only decodes N64 addresses as pointers, never PS2 ones.

- *(done: the launcher lists it first with a plain explanation; the Agents view shows each model's cost per match)* **The API helper first** (the owner's answer below): the helper that calls a model's API with the user's key is
  the one decompdecomp controls end to end, so the app recommends it first when starting helpers, explains it
  plainly (any provider: Anthropic, OpenAI, DeepSeek, OpenRouter, a local model), and its loop gets the same care
  as the CLI helpers' (prompt caching done; cost per model shown).

## 5. Shared knowledge agents keep up to date

*Done: `app/knowledge.py`, the `share_knowledge` tool, and "What agents learned" in the project's settings.*

**Problem.** Each project has instructions for agents, but the user has to write them, and the user mostly watches.

**Goal.** Agents propose additions to the project's instructions. A setting per project decides how they land:
**ask me** (the user accepts or rejects each one) or **automatic**.

Guard rails:
- Only general knowledge that speeds up every agent ("functions in this file take a context struct as the first
  argument").
- A proposal needs several agents noticing the same thing before it counts.
- Never compiler guesses ("it's this compiler"). Compiler questions go through the compiler map and range checks.

## 6. Plug-and-play consoles

*Done: PS1, GBA and DS removed (forks of their public decomps still open). The connector is
`consoles/__init__.py` (the `Console` class and the list of consoles); `consoles/n64/`, `consoles/ps2/`,
`consoles/gc_wii/` and `consoles/pc/` hold each console's code and guides, `consoles/public.py` the consoles only
public decomps cover, `consoles/template/` the package to copy; `arch/` (mips, ppc, x86, arm) the processor's facts.
The engine, briefs, twins, tools, prompts, the fork engine, rooms, saves, the export and the GUI's API go through the
console; the GUI takes names, status, layouts and compilers from `/api/state`'s `consoles`. PC's id is `pc`, with
`win32` as its alias for older projects. `tests/test_consoles.py` is the conformance test (N64, PS2 and PC pass on
the owner's machine; CI installs KMC GCC, EE-GCC, binutils-mips and m2c, so N64 and PS2 run there). CONTRIBUTING.md,
AGENTS.md and docs/CONVENTIONS.md say where a console's code is, how to improve one, add one, and add a compiler.*

*Left:* the drop zone's quick look at a file (`home.js`, `identifyHead`) still knows each console's magic numbers (a
new console adds its file there, or an `/api/identify` that asks `consoles.identify` replaces it); the shared
judge reads MIPS only (`rom/match.py`, `elfobj.py`'s HI16/LO16 pairing), so a ROM console on another processor needs
a judge too; `search_flags`' compiler families (`FLAG_FAMILIES`) and the compiler map's habits are by compiler, in
the engine; GameCube/Wii still has no end-to-end test anywhere (CodeWarrior is proprietary), and Wii no demo; PC no
export; the `decomp selftest` for proprietary compilers, whose result would become a console's status, isn't written.

**Problem.** Console support is spread over the engine, the importers, the judges and the GUI.

**Goal.** One connector the app knows, and one package per console that plugs into it: how to import a game, how
to judge a function, which compilers and flags, what the map shows, its demo game. Adding a console means adding
its package, without touching the engine.

**Why one folder each** (the owner): code that's easy to separate is easy to read, for people and for agents
alike. Someone fixing PS2 support opens `consoles/ps2/` and finds all of it there, without reading the engine.
New console code already goes there (`consoles/ps2/syscalls.py`).

**What's there today** (research, October 2026):

| Console | Import | Judge | Compilers | Compiler map | Classes | Export | Tested where |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| N64 | splat | link + compare | KMC, IDO 5.3/7.1 | ✓ | ✓ | ✓ | owner's machine |
| PS1 | EXE, disc, CHD, overlays | link + compare | gcc-2.7.2/2.8.1-psx | ✓ | ✓ | untested | nowhere |
| PS2 | ELF, disc | link + compare | 5 EE-GCCs, mwcps2 | ✓ | ✓ | ✓ | owner's machine (12 tests) |
| GBA | luvdis | ARM link + compare | agbcc | ✗ | ✗ | ✓ | nowhere |
| GameCube/Wii | dtk-template | objdiff | mwcc | ✗ | ✗ | the project | nowhere (unit tests only) |
| DS | ds-decomp | objdiff | mwccarm | ✗ | ✗ | the project | nowhere |
| PC | Ghidra + own delinker | objdiff (COFF) | MSVC 6/7/7.1 | ✗ | ✗ | ✗ | owner's machine |

- CI installs no compilers, so every end-to-end console test skips there. GameCube/Wii counts as "working" but
  nothing tests it end to end.
- About 145 `if platform/arch == ...` branches across 41 Python files, plus about 85 in the GUI scripts. The GUI
  re-does file detection in JavaScript (`home.js`), duplicating `detect.py`.
- Platform ids disagree: PS1 is `psx` and `ps`, DS is `nds` and `ds`; four separate name tables.

**Design (proposal).** Two layers, because the processor and the console vary independently:
- `arch/` (mips, ppc, arm, x86): what depends only on the processor: relocation masks, disassembly,
  hand-written-assembly detection, compiler-map habits, class scanning, the m2c target. An arch without a feature
  returns nothing, and the feature shows as missing, not as an `if`.
- `consoles/<id>/`: one package per console on one arch: names and aliases, status, compilers and default flags,
  setup packs, guides for agents, `sniff()` (how sure it is a file is this console's), `import_game()`, `judge()`
  (one of two shared judges: link and compare, or objdiff), `export()`, its demo game. A list in
  `consoles/__init__.py` registers them; the engine calls `consoles.get(platform)` and never compares platform
  names. The GUI gets names, compilers and features from `/api/consoles` and drops its own tables.

**Docs, at the end** (the owner): once the consoles live in their folders, CONTRIBUTING.md, AGENTS.md and
docs/CONVENTIONS.md say where each console's code is (`consoles/<console>/`, what each file does), how to improve an
existing console, and how to write a new one: copy the template package, fill in the connector's parts, add its demo
game, make the conformance test pass, add its column to ROADMAP.md's table. Written for people and agents alike.
Also how to **add a compiler to a console that exists** (the owner: PC has Visual C++ 6.0 to 7.1 today, someone
will want newer MSVC): the pinned toolchains.toml entry with checksums, how it runs on Linux, the licence gate for
proprietary ones, where the console lists its compilers and default flags, and the test that proves it builds
matching code.

**Trust.** One conformance test runs for every console: build its demo, detect it, import, judge known-matching C
(score 0) and wrong C (not 0), draft, export and rebuild byte for byte. CI installs the free compilers (pinned,
cached) so N64 (KMC), PS1, PS2 and GBA really run there. Proprietary compilers (mwcc, mwccarm, MSVC, mwcps2, IDO)
can't go in public CI: a `decomp selftest` the owner runs after accepting them in Setup, and its result becomes the
console's status in the app instead of a hand-written one. A console is "recommended" only once its test passes and
one real game has rebuilt byte for byte.

## 7. Moving projects to another computer

*Done (the app's side): `app/saves.py` (save, autosave to progress/, restore from a folder or git), `/api/save`, `/api/restore`, and a copied Decomp folder finds its projects (`find_moved_projects`). The buttons come with the UI overhaul.*

**Problem.** Changing distribution (or computer) loses the work. A project's progress lives in the app's database
(`~/Decomp/.decomp/decomp.db`), not only in its folder.

**Goal.**
- A project's folder holds everything that is the user's work: matched C, attempts worth keeping, notes, names,
  compiler ranges, the agents' shared knowledge, settings. Saved as plain files that diff well in git.
- **Re-import** from a folder, or from a git repository the user pushed it to. The app rebuilds its database rows
  from those files. The game itself is never in the folder: re-import asks for the user's own copy of the game, and
  checks it's the same one (its hash).
- Re-importing a project that's already there updates it instead of making a second one.

## 8. Startup and loading speed

**Problem.** When the app starts, the project list appears only after a lag. Opening a project shows an empty ROM
map at first, which fills in later.

**What's there today** (the owner's database, read-only): 172 MB plus a 90 MB write-ahead log, about 30,000
functions across four games (GTA San Andreas 12,627, one other game 11,877), 15,867 attempts, 10,521 events.

**Measured** (a copy of the owner's database and projects, October 2026):

| What | Time |
| :-- | --: |
| First `/api/state` (the home page waits for it): `agents.detect` runs `agy models`, `opencode models` and six `--version` | 3,750 ms |
| ROM map of GTA San Andreas after any match (the compiler map is rebuilt inside the request) | 710-800 ms |
| First claim brief on GTA San Andreas (class recovery 3 s, similar functions 1.2 s, twins 0.7 s) | 5,230 ms |
| Range checks after every match, GTA San Andreas (62 open checks) | 17,000 ms |
| A ROM compile + link + compare (the judge itself) | 20-60 ms |

With 16 simulated agents on two projects, the GUI's poll cycle went from 14 ms to 2.2-2.7 s (it polls every
second), `/api/projects` to 2.5-3.7 s, and an agent's claim to 2-3 s.

**Why the app stalls with two projects** (reproduced):
1. Every match starts its own range-check thread. Each queues on one lock and does 17 s of work: 62 times
   `SELECT * FROM functions`, dragging 73 MB of assembly text out each time. Matches come faster than that, so the
   queue never empties (92 threads queued at once).
2. All of it holds the one SQLite connection's lock, which every GUI request and agent call shares.
3. Each match makes the compiler map stale, and every waiting brief and map request rebuilds it at the same time
   (600-870 ms each). `quirks()` re-decodes every function's bytes on every rebuild. Settings JSON (193 KB) is
   parsed 4-11 times per request.

**Why the notifications flood** (from the code): opening a project resets the event counters and starts a poll, but
the other project's poll is still running (it takes longer than its 1-second interval under load), so the new one
does nothing. The next tick fetches the last 60 events and 80 attempts with the counters at zero, and shows each as
new.

**Also found:** `agy models` (run to list Antigravity's models) starts the MCP servers in Antigravity's config,
including the old `decomp mcp` bridge, which starts the app. One more reason the bridge goes.

**Fixes, ranked:**
1. Range checks: one pending run per project (debounced), the function rows fetched once with only the columns
   needed, verdicts cached by source hash. 17 s → under 0.5 s.
2. Compiler map: `quirks()` cached per function, one build at a time per project serving the last map meanwhile,
   and its colours sent in a later request than the map. 870 → about 250 ms, once per change.
3. GUI polls per project, and shows only events newer than the moment the project opened.
4. Agent detection in the background at start; pages render from `/api/projects` without waiting. 3.7 s → 50 ms.
5. Settings parsed once per request; later, suggestions and range checks in tables of their own.
6. Reads on their own connections (WAL allows concurrent readers), one writer; claim expiry on a timer, not in
   every live poll.
7. Brief caches warmed when a project opens (classes, cross-references, similar functions, twins prints). Cold
   brief 5.2 s → about 0.2 s.
8. Fork judge: builds are one at a time per project and the first try of a unit builds twice. About 4 judgments a
   second at most.
9. A WAL size limit and a checkpoint at start.

**Goal.** The window shows the project list at once, and a project's map at once. Slow extras (the compiler map's
colours, statistics) arrive afterwards without blocking. Measured with timings on the owner's real projects (on a
copy), before and after.

**Done:** fixes 1-7 and 9 (8, the fork judge, is open). On the same copy, after:

| What | Before | After |
| :-- | --: | --: |
| First `/api/state` | 3,750 ms | 15 ms (agents found later, by `/api/agents/detected`) |
| ROM map of GTA San Andreas after a match | 742-750 ms | 71-74 ms (colours from `/api/mapcolumns`) |
| Compiler map rebuild after a match; six asking at once | 930 ms; 1,850 ms | 145 ms; 4 ms (the last map answers) |
| First claim brief on GTA San Andreas | 3,230 ms | 380 ms (2.5 s built when the project opens) |
| Range recheck after a match, GTA San Andreas (62 open checks) | 17,300 ms | 68 ms |
| Settings parsed per claim brief / map / live poll | 12 / 4 / 5 | 1 / 0 / 0 |
| 16 simulated agents on two games: GUI poll cycle, median / p90 | 1,446 / 3,355 ms | 93 / 370 ms |
| ... `/api/projects` (every 5 s) | 2,043 / 4,401 ms | 7 / 48 ms |
| ... an agent's claim on GTA San Andreas | 1,849 / 4,003 ms | 215 / 1,877 ms |
| ... a compile that matches | 926 / 2,702 ms | 33 / 105 ms |

Still slow under load: a claim of a function with no machine draft yet runs m2c under one lock for the whole app
(`_seed_lock`, about 0.3 s each), so agents claiming at once wait in turn.

## 9. Giving agents an edge

The judge is strict, so even cheap models can do well; what decides the rate is how much each attempt learns and
how much code never needs an agent. Ranked:

1. *(the user's own games: done, `Twins.across_games`; library decomps built locally: not yet)* **Whole libraries for free.** A fingerprint index of library functions, tried as twins: across the user's own
   games, and from matching library decomps and libgcc/newlib built locally with the project's compiler (libultra
   on N64, the PS1 and PS2 SDK libraries). No agent tokens at all. Library sources are fetched only when the user
   asks, never bundled: SDK code is the console maker's copyright. Builds on `app/twins.py` and
   `elfobj.fingerprint()`. (M, plus L per library source)
2. **A real permuter.** Replace our six regex rewrites (`permuter.py`) with decomp-permuter (MIT, run as a pinned
   tool like m2c): rewrites on the parse tree, weighted per compiler, plus control-flow rewrites (inverted branches,
   while/for/do-while, if/ternary). Keep today's near-miss gate, and let an agent tidy the winning C. MIPS, PowerPC,
   ARM; not x86. (M)
   *How (researched, decomp-permuter @ 8556c81, MIT):* use it as a library, not as a program. Its
   `src/candidate.py` has `Candidate.from_source(source, EvalState(), fn_name, weights, rng_seed)`, then
   `randomize_ast()` and `get_source()`: pure Python with its own copy of pycparser (`perm_pycparser/`), weights per
   compiler in `default_weights.toml`. Install it as a pinned tool in its own environment (like m2c), and run a
   small generator in that environment: it reads the translation unit (the shared header made pycparser-friendly,
   as `seeds._context_for_m2c` does for m2c, plus the near miss's C) and writes N variants of the one function.
   Our own judge scores each (`app/permute.py` `_score`): no compile.sh, no objdump, no target.o. Run one worker
   per spare core. The current six regex rewrites (`permuter.py`) go.
   *Done:* `permuter_gen.py` (the generator, in the tool's environment), `app/permute.py` (batches of 64, from the
   turn's first C and the best so far in turn; workers shared by every project, one per spare core up to four, at
   a lower CPU priority; turns counted in the database). On nine near misses made from the demo ROM's functions it
   matched all nine, the regex rewrites five. Measured on 12 cores: one worker judges about 65 variants a second;
   past four workers the judge's own Python (about 5 ms of each variant, under the interpreter's lock) is the
   limit, and the app's requests wait behind it: running the judge in worker processes is what would lift it.
   The regex rewrites stay as the fallback, for C decomp-permuter can't parse (macro calls: m2c's `M2C_FIELD`, fork
   units, whose header isn't one file) and for computers without the tool. Open: an agent tidying the winning C
   (it's decomp-permuter's printing: §10's readable pass can).
3. **Struct layouts from field accesses.** Collect every load and store through each pointer argument and global,
   across the call graph; propose structs in the shared header; give them to m2c as context. Our own numbers: on GTA
   San Andreas, functions with virtual calls matched 7% of the time against 62% for the rest, because agents
   guessed layouts. Generalises `classes.py`. MIPS first. (M)
4. *(done in part: the part of the game where matches happen first, failing parts last; `network.py` REGION_BYTES)* **A learned scheduler.** Predict which function will match next from the project's own attempt log, instead of
   ordering by size; boost a function as soon as one of its look-alikes matches. (S)
5. **Model routing with early escalation.** Easy functions to cheap models. After about three failed attempts,
   hand a function to a stronger model or to the permuter instead of grinding on. Most matches come at the first
   attempt; the chance falls fast after the third. Ties in with §4's measurements. (S-M)
6. *(done for ROM projects: `network.py` _what_changed)* **Diffs that teach.** Show what the last change did against the attempt before (a three-way diff), the
   matching prefix, and where the first mismatch is. One small change per attempt works best. (S)
7. *(done as a grid, `search_flags`: each flag changed once, up to 12 compiles)* **Search over flags, not just compilers.** A search over compiler × flags scored by the judge's distance, only
   with compilers the user accepted. Today's compiler map varies compilers only. (M)
8. **Better look-alikes.** A second similarity measure (edit distance over opcodes) next to today's, and
   "submatch": shared runs of instructions, which find inlined helpers and macros (coddog, MIT/Apache, does this).
   Needs PowerPC and x86 opcode readers. (S)
9. **Decoders for data formats.** Tools that decode N64 display lists, PS2 GS/VIF packets and GameCube GX command
   lists for agents. (S-M each, check each decoder's licence)

10. **Make more of m2c.** Measured (October 2026, the owner's games): autopilot compiles m2c's draft of each
    function as it is and matched 1,352 of 4,844 (28%), about 41% of all matches. But helpers mostly ignore the
    draft: their first attempt is close to it (60% of tokens or more) for only 2-13% of functions on N64 and PS2
    (median similarity 23-29%). And the draft is weak where it could be strong:
    - It's made once, the first time a function is opened, and never again: types and structs added to the shared
      header later never reach it. Redraft when the header changes, and run autopilot again on what changed.
    - A `redraft` tool for agents: m2c run again with the agent's own declarations (a struct it worked out), so the
      draft follows what the agent learns.
    - Drafts exist only for functions someone opened (GTA San Andreas: 653 of 12,627). Draft in the background.
    - CodeWarrior PS2 code is read as EE GCC's (m2c has no CodeWarrior MIPS target).
    - **C++ games.** m2c always writes C. Its C++ mode only demangles CodeWarrior names and types CodeWarrior
      vtables (GameCube/Wii); for PS2's GCC it changes nothing, and stripped PS2 games have no names to demangle
      anyway. Agents can already write C++ (the judge compiles what isn't C as C++, `compiler.py`). The draft can
      meet them halfway: where the class recovery (`classes.py`) knows a function is a method of a class (it's in a
      vtable, or a constructor stores one), turn m2c's `func(void *arg0, ...)` into `Class::method(...)` with `this`
      and the class's known fields, in the brief and in the readable pass (§10).
    - The prompt says what the draft is good for: control flow and arithmetic are usually right, types and names
      usually wrong; start from it and fix those.

11. **Name PS2 system calls.** *(done: `consoles/ps2/syscalls.py`; 154 stubs in GTA San Andreas, 129 in Ridge Racer V)* The kernel's stubs (`addiu v1, N; syscall; jr ra`) are already found and marked
    hand-written (`handwritten.py`), but they keep names like `func_0012A0`. The number in `v1` says which kernel
    call it is: a table of our own (the numbers are public, documented e.g. in ps2sdk's permissively licensed
    headers) names every stub (FlushCache, CreateThread, ...) for free, and every function that calls one shows the
    real name in its brief. Stubs that only differ in `v1` are told apart by it. (S)

Not now: fine-tuned decompilation models. They're built for modern x86 and "does the same thing", not byte
matching, and frontier CLI agents do better here.

First three: libraries (1), the permuter (2), struct layouts (3), with the scheduler (4) alongside because it's
small.

## 10. From matching to readable

*Done (the app's side): `app/readable.py`, the `claim_readable` and `make_readable` tools, helpers started with the task "readable", and the overview's `readable` count. ROM projects. The GUI control comes with the UI overhaul.*

**Problem.** Matching C is often hard to read: `func_002C73A8`, `var_a0`, casts and odd orderings that only exist
to make the compiler emit the same bytes. People who want to port or mod a game need code they understand.

**Goal.** A readability pass, run by agents on matched functions: real names for functions, variables, fields and
globals, types and structs, comments that say what the code does. Every change is judged again: the function must
still match byte for byte, or the change is refused. Names and types also go into the shared header, so later
agents start from better context. The project shows two numbers: matched, and readable.

## 11. Built for contributors

The owner doesn't want to be the only maintainer. The rewrite should make the code easy to pick up: one package per
console (§6), tests that run in CI, CONTRIBUTING.md that says where to start, small issues labelled for newcomers,
and a development copy that never touches anyone's real projects (§1). People who enjoy the app can give their own
agents and tokens to improve it.

## 12. PS2 debug symbols (GitHub #3)

**Problem.** Some PS2 games ship with their debug information still in the ELF (Champions of Norrath, reported in
#3). decompdecomp notices the symbol table (`rom/ps2.py`: `"symbols"`) and then ignores it: every function is
func_..., every size is guessed by splat.

**Goal, for the rewrite's release** (the owner):
1. **The symbol table** (`.symtab`): real names and exact sizes for functions and data, given to splat as
   `symbol_addrs.txt` before it splits. C++ names stay mangled when they aren't valid C identifiers.
2. **The debug sections** (`.mdebug`, STABS inside ECOFF, what Sony's GCC and SN Systems wrote; DWARF where a game
   has it): types, structs and classes with their fields, function prototypes, local variable names, source file
   names. Types go into the shared header; source files become the project's units; prototypes and locals go into
   each function's brief. This is what agents guess worst today (§9.3).
3. Tested on the homemade PS2 demo built with `-g`, never on a real game in the repository.

**Done** (`consoles/ps2/`: `symtab.py`, `mdebug.py`, `stabs.py`, `cheader.py`, `debuginfo.py`; the demo's
`build.py --debug`; `tests/test_ps2_debug.py`): names and sizes from the symbol table; from STABS the source files
as units, prototypes and where parameters and locals live in the brief, every type and global in the first header
(checked with the project's compiler first). DWARF (CodeWarrior's `.debug`, later GCCs' `.debug_info`) is
recognised and named in the import log, not read yet. Still open: C++ methods in the header (classes are structs of
their data), callees' prototypes in the brief, and a header too big to read whole on a game with thousands of types
(the brief could carry only the types a function uses).

## 13. A stranger's test, at the very end

When everything above is done, an agent uses the app the way a new user would, without reading its source: installs
the development copy, opens the demo game, starts helpers, closes and reopens the window, exports, breaks things on
purpose. It reports everything it finds in detail: bugs, confusing screens, missing features, anything a stranger
gets stuck on. Its findings go into issues or straight into fixes.

## 14. Finishing the rewrite

In this order, nothing skipped:
1. Merge the UI overhaul (§2, §3) and the consoles (§6) when their agents finish; the suite passes after each.
2. The real permuter (§9.2, researched and prototyped above), then what's left of §9 (struct layouts, library
   decomps built locally, the m2c items of §9.10, the brief's types per function from §12's risks).
3. The stranger's test (§13), its findings fixed.
4. The release's version: 1.0.3-alpha (the owner told the issue reporters so), bumped in the four places.
5. Every Markdown file describes the new decompdecomp: README, AGENTS.md, CONTRIBUTING.md, docs/CONVENTIONS.md,
   docs/GUIDE.md, ROADMAP.md (its table and what's done). This file is deleted.
6. The app, started as the development copy for the owner to try before anything is released.
7. Squash-merged into main by the owner, released; the owner answers #3 and #4 when it's out.
8. After the release: an agent researches how to support Windows well (§1). `wsl.py` (running Linux compilers through WSL
   from a native Windows app) is still in place, inactive on Linux, for that research to keep or remove.
9. On the owner's computer: the old 1.0.0-alpha .deb removed (`sudo apt remove decompdecomp`), if it isn't yet.

## Questions

Answers go here as they come.

- **Closing the window while helpers work:** ask each time: "stop them and quit", or "keep them working in the
  tray". The tray icon reopens the window or quits. (§1)
- **Window:** native, on the system's WebKitGTK 4.1, driven by a small window program the system's `python3` runs
  (`python3-gi`, `gir1.2-webkit2-4.1`): the app's own Python (uv's) can't load the system's GTK bindings, and pip
  can't install them without C build headers. Tauri was considered: on Linux it uses the same WebKitGTK and adds
  Rust. The .deb depends on both packages; the AppImage checks for them at start and, if they're missing, says the
  install command for the user's distribution and quits (no browser fallback). Later, maybe a Flatpak on the GNOME
  runtime, which has WebKitGTK. Linux is the platform: Windows (WSL) and macOS get no native window in the rewrite;
  contributors can add one (pywebview there). (§1)
- **Auto-restart:** always, with backoff. After any stop or crash the helper starts again; on a usage limit it
  waits until the limit resets. Five fast failures in a row pause it, and the app shows why. (§4)
- **Shared knowledge needs two different models.** Two Claude Opus helpers saying the same thing is one opinion;
  Claude Opus and GPT saying it is two. The app tells the user this where the setting is, and recommends running
  helpers of several models: variety and competition between models find more, and make shared knowledge possible.
  (§5)
- **Outside agents:** none. Only helpers the app starts. The public MCP bridge goes; MCP stays only as the app's
  private wiring to the CLI helpers it runs. (§1)
- **Consoles:** in this repo only, one package per console, with a template. (§6)
- **Order:** §1 standalone app and §4 agents first, then §3 notifications, §2 UI, §5 knowledge, §6 consoles.
- **Consoles:** N64, PS2, PC (Windows games) and GameCube/Wii stay, each verified after the rewrite. GameCube/Wii
  stays although nothing tests it end to end: issues that turn up are for contributors to fix too. PS1, GBA and DS
  are removed (the git history keeps them for anyone who wants to bring one back as a console package). (§6)
- **The API helper becomes the main helper** (§4): it talks to a model's API directly with the user's key
  (Anthropic, OpenAI, DeepSeek, OpenRouter...), so decompdecomp controls its whole loop: prompt, tools, caching,
  cost. CLI helpers stay, but their updates can't break the app's main path.
- **The community:** decompdecomp protects itself legally (nothing of the game leaves the user's computer, no game
  data in the repository) and doesn't shape itself around anyone's opinion of AI.
