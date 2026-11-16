# decompdecomp's TUI: `decomp serve` in a terminal

`decomp serve` runs decompdecomp where there's no desktop to open a window on: the app itself (the server, the
agents, the autopilot) with a text front end in its terminal, and the full GUI paired to a browser through an SSH
tunnel. [The guide](GUIDE.md#on-a-server-decomp-serve) covers how you run it and reach it; this page says what the
TUI shows and, for anyone changing it — human or agent — how it works inside.

## What you see

It opens on a one-time link to pair a browser (`Escape` closes it, `b` shows it again). Then three screens, switched
with `1` `2` `3` (or `Tab`). The line at the top names the app, the project and the browser address; the line at the
bottom lists the current screen's keys. `?` lists them all.

**1 · Projects.** One row per project: title, platform, the share of its code matched (in bytes, as the window
shows it — a project of small matches reads low), matched over the functions C can make, the helpers working on it
now, and why a project can't be read, when it can't. `Enter` opens the project's map; `2` straight away opens the one
under the cursor. With no projects, it says which folder it looked in.

**2 · Map.** The same map the window draws, filling the screen (every row it has free: 36 on a 41-line terminal). A
small game gets a block per function, as big as lets them all fit (Demo Quest 64's 95 functions: blocks 9 wide and 4
high, 11 to a row); a big game packs several functions into a one-character cell, which shows the status most of
them hold. The colours are the window's own, read from its stylesheets:

| Status | Means |
| --- | --- |
| polished | matched, and rewritten readable |
| matched | rebuilt byte for byte |
| in progress | an agent is working on it now |
| tried | agents tried; no match yet |
| not C | no C makes it (set aside) |
| open | nobody tried |

The legend under the map shows each status's colour and how many functions hold it. On a terminal without 256
colours (the Linux console, `screen` with a plain `TERM`), each status gets one basic colour instead.

The line under the map describes the cell under the cursor; below it are the project's numbers and the legend.
`← ↓ ↑ →` and `h j k l` move; `Home`/`End` go to the row's ends, `g`/`G` to the first and last cell. `Enter` on a cell of
one function shows what the map knows about it; on a cell of several, a picker (type to filter) chooses which (the
list scrolls inside the screen). `/` searches names: hits list as you type, `↑ ↓` pick one, `Enter` goes there, and
`n` / `N` step through the others.

**3 · Agents.** Who works on what now (agent, function, best score, tries, for how long); the day's board — each
agent's compiles and matches, with a sparkline of matches per hour over the last day; and the feed: each try in the
open project, and the events of every project, as they happen, newest at the bottom. `f` stops the feed following
(to read back) and starts it again. `L` shows the end of the app's log.

**Everywhere.** `b` shows a fresh one-time link for the browser; `r` refreshes now; `q` (or `Ctrl-C`, `Ctrl-Q`) stops
decompdecomp and its agents — it asks first, because leaving the TUI stops the app. To leave it running, detach from
`screen` (`Ctrl-A d`) or `tmux` (`Ctrl-B d`) instead. `Ctrl-P` opens Textual's commands, among them a light theme for
a light terminal.

**Copying.** The TUI doesn't use the mouse: it's driven from the keyboard, and the mouse stays your terminal's. Select
the link (or anything) and copy it as you would anywhere, inside `screen` and `tmux` too. On the link screen, `c` also
copies it, for terminals that take copy requests from programs (OSC 52): Alacritty does by default, iTerm2 once
"Applications in terminal may access clipboard" is on, macOS Terminal doesn't. `tmux` passes it on with
`set -g set-clipboard on`; GNU `screen` doesn't.

This first version only reads. Everything that changes a project — imports, settings, releases, notes, steering the
agents — is done in the browser (or the window).

## How it works inside

For changing the TUI, or changing the app's API without breaking it.

**It is one more client of the app's own HTTP API.** The TUI runs in the foreground of `decomp serve`, the server in
a background thread of the same process (`cli._serve`), and talks to it over HTTP on `127.0.0.1` carrying the
per-launch secret in its user agent — exactly like the window's JavaScript and `decomp`'s own commands. It only
reads: the client (`tui/client.py`) has `get()` for JSON and `text()` for the log, and nothing that writes.

**The files** (`src/decompnet/app/tui/`):

| File | What's in it |
| --- | --- |
| `__init__.py` | `run()`: the TUI when there's a terminal, else `None` and `decomp serve` prints the link; `release()` |
| `client.py` | the two GETs, and `READS`: every field the TUI takes from each answer |
| `app.py` | the Textual App, the keys that work everywhere, `Base.fetch` (every request, in a thread) |
| `colors.py` | the map's colours, read from `static/tokens.css` and `static/app.css` |
| `projects.py` | screen 1 |
| `mapview.py` | screen 2: the map, the picker, the info panel, search |
| `agents.py` | screen 3: working now, the board, the feed, the log |
| `mapgrid.py` | the map's layout and a function's status — plain Python, no Textual |

**Asking the app.** Every request goes through `Base.fetch`: the GET runs in a worker thread and its answer is applied
on the screen afterwards, so a slow or absent app never freezes the terminal. Only the screen being shown polls (a
box shown over it, like the browser link it opens on, leaves it polling: Textual's `is_current` counts a screen still
visible around a `ModalScreen`); one request per kind is out at a time. A screen that needs a first answer to start
from (the feed's start, `/api/events` with no `after`) asks it again on each poll until the app answers: the screen
can first be shown as `decomp serve` starts. An error in a screen is logged to `decomp.log` (and kept in `app.problems`,
which the tests check is empty) and the TUI carries on.

**Ending.** In the TUI, `q` (or `Ctrl-C`, `Ctrl-Q`) and its "stop everything?" end the app; a signal from outside —
`kill`, the terminal going away (`SIGHUP`), and with no terminal Windows' `Ctrl-Break` — ends it gracefully without
asking (`cli._serve`, `stop_on`). If the TUI ends any other way (a bug), `decomp serve` logs it, prints the browser link
and keeps serving. When the app ends from elsewhere — Quit in the browser, `decomp stop`, an update's restart —
`release()` closes the TUI first, so the terminal is put back as it was. `tests/test_tui.py` runs a real `decomp serve`
in a pseudo-terminal and checks both signals end it this way.

**The layout** (`mapgrid.layout`): the map fills the lines the screen has free (all but the top line and the four
below the map; it follows a resize) by the terminal's width, `C` characters, functions in the API's order (address,
then unit). More functions than characters: one character a cell, cell `i` holding functions `floor(i × n / C)` to
`floor((i + 1) × n / C) − 1`, every cell full — 15,000 functions on an 80 × 24 terminal (19 rows of map) puts 9 or
10 in each. Fewer: one function a cell, drawn as a block of characters as big as lets them all fit (`blocks`):
between as wide as it's high and three times as wide, nearest twice as wide (a terminal's characters are about twice
as high as wide, so it looks square), with a column of space after it and, from 3 lines high, a line under it. The
cursor moves by the layout's columns, and the line under the map counts cells from 1. A function's status follows
the window's `cellColor()` (`static/js/map.js`) in its order: matched (polished apart), in progress, not C, tried,
open. Claims and new attempts arrive with `/api/live` and are laid onto the map in place, as the window's
`applyClaims()` does: the map's signature leaves them out on purpose (a game at work would re-send the whole map
every second). The first `/api/live` answer of a project only says where its attempts are up to; they're in the map
already.

**The colours** (`colors.py`): the window's own, read from its stylesheets when the TUI starts and turned into RGB
(the window lets the browser do that; here it's the few forms the map's colours use: `var()`, `oklch()` and
`color-mix(in oklch, …)`). A change to the window's colours reaches the TUI with nothing to copy. The cursor is a `◆`
(or `*`), black or white over its cell, whichever contrasts.

**Keeping it in step with the API.** The project has many contributors, so an API change must not break the TUI
quietly. `tests/test_tui.py` starts the real app on a project seeded straight into the database (no compiler, no
game: it never skips) and:

- checks every field in `client.READS` is in the real answers with its type, and that `/api/map` has no status the
  TUI can't paint (`client.MAP_STATUSES`) — a change the TUI depends on fails here, naming the route and field;
- drives the three screens with Textual's pilot against that app and checks what they show.

A change to a route the TUI doesn't read concerns none of this. When a screen starts reading a new field, add it to
`READS`, and make sure the seed in `tests/test_tui.py` gives it a value.

Textual is pinned below its next major version (`pyproject.toml`); raising the pin is a change of its own, with these
tests run against the new version.

## Roadmap

In rough order: function detail (source, assembly, diffs); actions on functions (the keys `d` `v` `t` `x` `o` `p` `e`
`R` are kept free for them); steering the agents; setup as a workflow; text boxes with Tab completion wherever the
window opens a file chooser; import and restore; the compiler map's second colouring; a list of the paired browsers,
with a way to end one (a session now lasts until the app stops). An open question is whether map
cells should weigh functions by their instruction count instead of counting them equally.
