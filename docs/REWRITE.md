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

## 3. One notification centre for the whole app

**Problem.** With two projects running agents, the app stalls. Opening the other project freezes it for a moment,
then floods it with notifications.

**Likely why.** Notifications are per project: the view of a project keeps its own feed and replays the backlog
when you open it. To confirm in the rewrite.

**Goal.** One app-wide feed. Each entry shows the project (colour-coded), the agent and what happened ("Ridge
Racer V · gemini-3.8-flash-2 matched func_0012A0"). Bursts collapse into one line ("+12 more matches"). Opening a
project never replays anything.

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
- **Efficiency.** Shorter prompts, fewer wasted tool calls. To measure first (tokens and tool calls per match, per
  model).

## 5. Shared knowledge agents keep up to date

**Problem.** Each project has instructions for agents, but the user has to write them, and the user mostly watches.

**Goal.** Agents propose additions to the project's instructions. A setting per project decides how they land:
**ask me** (the user accepts or rejects each one) or **automatic**.

Guard rails:
- Only general knowledge that speeds up every agent ("functions in this file take a context struct as the first
  argument").
- A proposal needs several agents noticing the same thing before it counts.
- Never compiler guesses ("it's this compiler"). Compiler questions go through the compiler map and range checks.

## 6. Plug-and-play consoles

**Problem.** Console support is spread over the engine, the importers, the judges and the GUI.

**Goal.** One connector the app knows, and one package per console that plugs into it: how to import a game, how
to judge a function, which compilers and flags, what the map shows, its demo game. Adding a console means adding
its package, without touching the engine.

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

**Trust.** One conformance test runs for every console: build its demo, detect it, import, judge known-matching C
(score 0) and wrong C (not 0), draft, export and rebuild byte for byte. CI installs the free compilers (pinned,
cached) so N64 (KMC), PS1, PS2 and GBA really run there. Proprietary compilers (mwcc, mwccarm, MSVC, mwcps2, IDO)
can't go in public CI: a `decomp selftest` the owner runs after accepting them in Setup, and its result becomes the
console's status in the app instead of a hand-written one. A console is "recommended" only once its test passes and
one real game has rebuilt byte for byte.

## 7. Moving projects to another computer

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

**Suspects, to measure before changing anything:**
- The ROM map's request (`engine/gui.py`, `function_map`) runs the compiler map for every function of the game
  (`_map_columns`) each time, as part of the same answer as the map itself.
- Each project's settings are one JSON column, parsed again by most requests (`cfg(proj)`); suggestions, range
  checks and notes grow inside it.
- The overview asks for the latest event with `WHERE project_id=?`, and no index has `project_id` first.
- The write-ahead log isn't checkpointed, so it grew to 90 MB.

**Goal.** The window shows the project list at once, and a project's map at once. Slow extras (the compiler map's
colours, statistics) arrive afterwards without blocking. Measured with timings on the owner's real projects (on a
copy), before and after.

## 9. Giving agents an edge

The judge is strict, so even cheap models can do well; what decides the rate is how much each attempt learns and
how much code never needs an agent. Ranked:

1. **Whole libraries for free.** A fingerprint index of library functions, tried as twins: across the user's own
   games, and from matching library decomps and libgcc/newlib built locally with the project's compiler (libultra
   on N64, the PS1 and PS2 SDK libraries). No agent tokens at all. Library sources are fetched only when the user
   asks, never bundled: SDK code is the console maker's copyright. Builds on `app/twins.py` and
   `elfobj.fingerprint()`. (M, plus L per library source)
2. **A real permuter.** Replace our six regex rewrites (`permuter.py`) with decomp-permuter (MIT, run as a pinned
   tool like m2c): rewrites on the parse tree, weighted per compiler, plus control-flow rewrites (inverted branches,
   while/for/do-while, if/ternary). Keep today's near-miss gate, and let an agent tidy the winning C. MIPS, PowerPC,
   ARM; not x86. (M)
3. **Struct layouts from field accesses.** Collect every load and store through each pointer argument and global,
   across the call graph; propose structs in the shared header; give them to m2c as context. Our own numbers: on GTA
   San Andreas, functions with virtual calls matched 7% of the time against 62% for the rest, because agents
   guessed layouts. Generalises `classes.py`. MIPS first. (M)
4. **A learned scheduler.** Predict which function will match next from the project's own attempt log, instead of
   ordering by size; boost a function as soon as one of its look-alikes matches. (S)
5. **Model routing with early escalation.** Easy functions to cheap models. After about three failed attempts,
   hand a function to a stronger model or to the permuter instead of grinding on. Most matches come at the first
   attempt; the chance falls fast after the third. Ties in with §4's measurements. (S-M)
6. **Diffs that teach.** Show what the last change did against the attempt before (a three-way diff), the
   matching prefix, and where the first mismatch is. One small change per attempt works best. (S)
7. **Search over flags, not just compilers.** A search over compiler × flags scored by the judge's distance, only
   with compilers the user accepted. Today's compiler map varies compilers only. (M)
8. **Better look-alikes.** A second similarity measure (edit distance over opcodes) next to today's, and
   "submatch": shared runs of instructions, which find inlined helpers and macros (coddog, MIT/Apache, does this).
   Needs PowerPC and x86 opcode readers. (S)
9. **Decoders for data formats.** Tools that decode N64 display lists, PS2 GS/VIF packets and GameCube GX command
   lists for agents. (S-M each, check each decoder's licence)

Not now: fine-tuned decompilation models. They're built for modern x86 and "does the same thing", not byte
matching, and frontier CLI agents do better here.

First three: libraries (1), the permuter (2), struct layouts (3), with the scheduler (4) alongside because it's
small.

## Questions

Answers go here as they come.

- **Closing the window while helpers work:** ask each time: "stop them and quit", or "keep them working in the
  tray". The tray icon reopens the window or quits. (§1)
- **Window:** native, pywebview on the system's WebKit. The .deb depends on webkit2gtk; the AppImage needs it
  installed or bundled. (§1)
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
