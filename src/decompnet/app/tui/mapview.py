"""Screen 2: the function map (mapgrid.py lays it out), the line under it describing the cell under the cursor, the
project's overview numbers, and a legend; Enter on a cell of several functions opens a picker, `/` searches names.
Claims and new attempts arrive from /api/live and are laid onto the map in place, as the window does (its map
signature leaves them out on purpose: a map of a game at work would come again every second). Both poll only while
the map is shown.
"""

from __future__ import annotations

import sys
import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import mapgrid
from .app import Base, plain

FALLBACK_WIDTH = 80  # before the first layout, or when the terminal hasn't said
CURSOR = "◆" if "◆".encode(getattr(sys.stdout, "encoding", "") or "ascii", "ignore") else "*"  # a console without it


MAP_POLL, LIVE_POLL = 5, 1.5
NAMED = {"black": 0.0, "blue": 0.07, "magenta": 0.15, "red": 0.21, "green": 0.15, "cyan": 0.25,
         "yellow": 0.6, "white": 0.9}  # the text console's 8: bright enough to put a black or white diamond over


def luminance(color: str) -> float:
    """A cell background's brightness (0–1): the cursor is drawn black or white over it, whichever reads."""
    if not color.startswith("#"):
        return NAMED.get(color, 0.5)

    def lin(u: float) -> float:
        return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4

    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def map_text(grid: mapgrid.Layout, statuses: list[str], cursor: int, colors: dict) -> Text:
    """The map as Textual shows it: every cell a block of its status colour (one character, or bw × bh with a column
    of space after it and, when it's 3 lines or more, a line under it), the cursor a diamond in the middle of its own
    (black or white, whichever contrasts; * where the terminal can't show the diamond)."""
    out = Text()
    fill_w = grid.bw - 1 if grid.bw > 1 else 1  # the coloured part of a block; the rest is the gap
    fill_h = grid.bh - 1 if grid.bh > 2 else grid.bh
    lines_out = 0
    for top in range(0, len(statuses), grid.cols):
        row = range(top, min(top + grid.cols, len(statuses)))
        for y in range(grid.bh):
            if lines_out:
                out.append("\n")
            lines_out += 1
            if y >= fill_h:
                continue  # the line between rows of blocks
            for i in row:
                color = colors[statuses[i]]
                if i == cursor and y == (fill_h - 1) // 2:
                    mid = (fill_w - 1) // 2
                    out.append(" " * mid, style=f"on {color}")
                    out.append(CURSOR, style=f"{'black' if luminance(color) > 0.4 else 'white'} on {color}")
                    out.append(" " * (fill_w - mid - 1), style=f"on {color}")
                else:
                    out.append(" " * fill_w, style=f"on {color}")
                if grid.bw > 1:
                    out.append(" ")
    return out


def legend_text(statuses: list[str], colors: dict) -> Text:
    """One line: each status's colour and how many functions hold it."""
    out = Text(" ")
    for status in mapgrid.ORDER:
        n = statuses.count(status)
        if n:
            out.append("■ ", style=colors[status]).append(f"{n} {status}   ")
    return out


class Picker(ModalScreen):
    """Enter on a cell holding several functions: which of them. Typing filters the list."""

    BINDINGS = [("escape", "dismiss", "close"), Binding("up", "up", None, priority=True),
                Binding("down", "down", None, priority=True), ("enter", "choose", "open")]

    def __init__(self, functions: list[dict]):
        super().__init__()
        self.functions = functions
        self.shown = list(functions)

    @staticmethod
    def line(fn: dict) -> Text:
        best = f" · best {fn['best']}" if fn.get("best") is not None else ""
        return plain(f" {fn['name']} · {mapgrid.status_of(fn)} · {fn.get('insns', 0)} insns{best}")

    def repopulate(self) -> None:
        self.query_one(OptionList).clear_options()
        for i, fn in enumerate(self.shown):
            self.query_one(OptionList).add_option(Option(self.line(fn), id=str(i)))
        if self.shown:
            self.query_one(OptionList).highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        needle = (event.value or "").casefold()
        self.shown = [fn for fn in self.functions if needle in fn["name"].casefold()]
        self.repopulate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_choose()  # Enter in the filter is Enter on the highlighted function

    def action_up(self) -> None:
        list_ = self.query_one(OptionList)
        if list_.highlighted is not None and list_.highlighted > 0:
            list_.highlighted -= 1

    def action_down(self) -> None:
        list_ = self.query_one(OptionList)
        if list_.highlighted is not None and list_.highlighted < len(self.shown) - 1:
            list_.highlighted += 1

    def action_choose(self) -> None:
        picked = self.query_one(OptionList).highlighted
        self.dismiss(self.shown[picked]["name"] if picked is not None and self.shown else None)

    def action_dismiss(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        yield Static(plain(f"  the cell's {len(self.functions)} functions — type to filter, ↑ ↓ pick, Enter opens"),
                     id="picker-hint")
        yield Input(placeholder="filter")
        yield OptionList(*[Option(self.line(fn), id=str(i)) for i, fn in enumerate(self.shown)])


class InfoPanel(ModalScreen):
    """A function, as far as the map row already knows it (code and diffs are on the roadmap)."""

    BINDINGS = [("escape", "dismiss", "close"), ("enter", "dismiss", "close")]

    def __init__(self, fn: dict):
        super().__init__()
        self.fn = fn

    def compose(self) -> ComposeResult:
        fn = self.fn
        at = time.strftime("%Y-%m-%d %H:%M", time.localtime(fn["matched_at"])) if fn.get("matched_at") else "—"
        fields = [("name", fn["name"]), ("address", f"{fn.get('vram') or 0:x}" if fn.get("vram") else "—"),
                  ("unit", fn.get("unit") or "—"), ("size", f"{fn.get('size') or 0} bytes"),
                  ("instructions", fn.get("insns", 0)), ("status", mapgrid.status_of(fn)),
                  ("best score", fn.get("best") if fn.get("best") is not None else "—"),
                  ("attempts", fn.get("attempts", 0)), ("agent", fn.get("agent") or "—"),
                  ("matched by", fn.get("matched_by") or "—"), ("matched at", at)]
        yield Static(plain("\n".join(f"  {k:<12} {v}" for k, v in fields)), id="info")


class Search(ModalScreen):
    """`/`: function names, case-insensitive substring, listed as you type; Enter goes to one."""

    BINDINGS = [("escape", "dismiss", "close"), ("enter", "choose", "go"),
                Binding("up", "up", None, priority=True), Binding("down", "down", None, priority=True)]

    def __init__(self, rows: list[dict]):
        super().__init__()
        self.rows = rows
        self.hits: list[dict] = []
        self.needle = ""

    SHOWN = 50  # hits listed; n and N go through all of them

    def on_input_changed(self, event: Input.Changed) -> None:
        self.needle = event.value or ""
        needle = self.needle.casefold()
        self.hits = [fn for fn in self.rows if needle in fn["name"].casefold()]
        list_ = self.query_one(OptionList)
        list_.clear_options()
        for i, fn in enumerate(self.hits[:self.SHOWN]):
            list_.add_option(Option(plain(f" {fn['name']} · {mapgrid.status_of(fn)}"), id=str(i)))
        if self.hits:
            list_.highlighted = 0

    def action_up(self) -> None:
        list_ = self.query_one(OptionList)
        if list_.highlighted:
            list_.highlighted -= 1

    def action_down(self) -> None:
        list_ = self.query_one(OptionList)
        if list_.highlighted is not None and list_.highlighted < list_.option_count - 1:
            list_.highlighted += 1

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_choose()  # Enter in the search box goes to the highlighted hit

    def action_choose(self) -> None:
        picked = self.query_one(OptionList).highlighted
        self.dismiss((self.needle, self.hits, picked) if picked is not None and self.hits else None)

    def action_dismiss(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        yield Input(placeholder="function names")
        yield OptionList()


class MapScreen(Base):
    """The map of one project, its claims and attempts laid on as they happen."""

    BINDINGS = [("left", "cursor_left", None), ("right", "cursor_right", None), ("up", "cursor_up", None),
                ("down", "cursor_down", None), ("h", "cursor_left", None), ("l", "cursor_right", None),
                ("j", "cursor_down", None), ("k", "cursor_up", None),
                ("home", "row_start", None), ("end", "row_end", None), ("g", "first", None), ("G", "last", None),
                ("enter", "open", "the cell"), ("/", "search", "search"), ("n", "again(1)", None),
                ("N", "again(-1)", None)]

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.rows: list[dict] = []
        self.sig: str | None = None
        self.map_cells: list[range] = []
        self.grid = mapgrid.Layout([], FALLBACK_WIDTH)  # how the cells fill the screen (mapgrid.layout)
        self.statuses: list[str] = []
        self.cursor = 0
        self.selected: int | None = None  # the function the cursor is on, in its cell
        self.slug: str | None = None
        self.find: tuple[str, list[int], int] | None = None  # needle, hits (row numbers), where in them
        self.claims: list[dict] = []  # the last claims /api/live brought, laid onto every map that comes
        self.last_event = 0
        self.last_attempt = 0
        self.primed = False  # the first live answer of a project only says where its attempts are up to
        self.overview_line = ""
        self.note: str | None = None  # said under the map in place of the cell's description, until the cursor moves

    def body(self) -> list:
        return [Static("", id="map", classes="maparea"), Static("", id="cell-line"),
                Static("", id="overview-line"), Static("", id="legend-line")]

    def on_mount(self) -> None:
        self.set_interval(LIVE_POLL, self.poll_live)
        self.set_interval(MAP_POLL, self.poll_map)

    def on_screen_resume(self) -> None:
        """Shown (again): a project was opened into it, or the user came back. Shown with no project open yet (`2`
        before Enter on screen 1): the project under screen 1's cursor, or how to pick one."""
        if self.slug is None:
            p = self.app.screens["projects"].highlighted()
            if p is not None and not p.get("broken"):
                self.open_project(p.get("slug"))
                self.call_after_refresh(self.relayout)
            else:
                self.say("no project open: pick one on screen 1 (press 1, then Enter)")
            return
        self.refresh_now()
        self.call_after_refresh(self.relayout)  # (the terminal may have changed size while another screen was up)

    def on_resize(self) -> None:
        # the terminal's size is the map's (once the new layout is done); the cursor stays on its function
        self.call_after_refresh(self.relayout)

    def refresh_now(self) -> None:
        if not self.is_mounted or self.slug is None:
            return
        self.sig = None  # ask for the whole map again, not the "nothing changed" of a poll
        self.poll_map(always=True)
        self.poll_live(always=True)

    def open_project(self, slug: str) -> None:
        if slug == self.slug:
            return
        self.slug, self.rows, self.sig, self.find, self.selected, self.cursor = slug, [], None, None, None, 0
        self.note = None
        self.map_cells, self.statuses, self.claims, self.overview_line = [], [], [], ""
        self.last_event = self.last_attempt = 0
        self.primed = False
        if self.is_mounted:
            self.query_one("#map", Static).update("")
            self.say("asking for the map")
        self.refresh_now()

    # -- the two polls

    def poll_map(self, always: bool = False) -> None:
        if self.slug is None or not (always or self.polling()):
            return
        slug, since = self.slug, self.sig
        self.fetch("map", lambda: self.client.get("/api/map", slug=slug, since=since), lambda d: self.took_map(slug, d))

    def took_map(self, slug: str, data) -> None:
        if slug != self.slug:
            return  # (an answer for the project the screen was on before)
        if data is None:
            self.say("no map came: the app isn't answering, or can't read this project (see screen 1)")
        elif not data.get("same"):
            self.sig = data["sig"]
            keys = data["keys"]
            self.rows = [dict(zip(keys, row)) for row in data["rows"]]
            mapgrid.apply_claims(self.rows, self.claims)
            self.relayout()

    def poll_live(self, always: bool = False) -> None:
        if self.slug is None or not (always or self.polling()):
            return
        slug, e, a = self.slug, self.last_event, self.last_attempt
        self.fetch("live", lambda: self.client.get("/api/live", slug=slug, e=e, a=a),
                   lambda d: self.took_live(slug, d))

    def took_live(self, slug: str, data) -> None:
        if slug != self.slug or not data:
            return
        self.claims = data.get("claims") or []
        mapgrid.apply_claims(self.rows, self.claims)  # who works on what, now: laid onto the map in place
        if self.primed:  # each try since the last answer, without asking for the map again
            by_name = {fn["name"]: fn for fn in self.rows}
            for at in data.get("attempts") or []:
                if fn := by_name.get(at["function"]):
                    fn["attempts"] = (fn.get("attempts") or 0) + 1
                    if at.get("score") is not None and (fn.get("best") is None or at["score"] < fn["best"]):
                        fn["best"] = at["score"]
            if data.get("cut"):  # more attempts than one answer holds: the map's counts come again
                self.sig = None
                self.poll_map(always=True)
        self.primed = True
        if data.get("events"):
            self.last_event = data["events"][-1]["id"]
        if data.get("attempts"):
            self.last_attempt = data["attempts"][-1]["id"]
        if overview := data.get("overview"):
            working = len(self.claims)
            self.overview_line = (f" {overview.get('functions', 0)} functions · {overview.get('matched', 0)} matched"
                                  f" · {overview.get('set_aside', 0)} set aside"
                                  + (f" · {working} at work" if working else ""))
            self.app.say_project(str(overview.get("title") or self.slug))
        self.relayout()

    # -- layout and drawing

    def relayout(self) -> None:
        if not self.rows or not self.is_mounted:
            return
        width, rows = self.width(), self.rows_free()
        stay_on = self.selected if self.selected is not None else (
            self.map_cells[self.cursor].start if self.map_cells and self.cursor < len(self.map_cells) else 0)
        self.grid = mapgrid.layout(len(self.rows), width, rows)
        self.map_cells = self.grid.cells
        self.statuses = [mapgrid.status_of(fn) for fn in self.rows]
        self.cursor = mapgrid.cell_of(self.map_cells, min(stay_on, len(self.rows) - 1))
        self.selected = (min(stay_on, len(self.rows) - 1) if self.selected is not None
                         else self.map_cells[self.cursor].start)
        self.draw()

    def draw(self) -> None:
        """The map and the lines under it (the note, while there is one, in place of the cell's description)."""
        if not self.map_cells:
            return
        colors = self.app.colours()
        self.query_one("#map", Static).update(map_text(self.grid, self.cell_statuses(), self.cursor, colors))
        self.query_one("#cell-line", Static).update(plain(self.note or self.describe()))
        self.query_one("#overview-line", Static).update(plain(self.overview_line))
        self.query_one("#legend-line", Static).update(legend_text(self.statuses, colors))

    def cell_statuses(self) -> list[str]:
        return [mapgrid.cell_status([self.statuses[i] for i in held]) for held in self.map_cells]

    def describe(self) -> str:
        """The line under the map: the cell under the cursor — one function, or the run a full cell holds."""
        held = self.map_cells[self.cursor]
        fns = [self.rows[i] for i in held]
        chosen = self.rows[self.selected] if self.selected is not None and self.selected in held else fns[0]
        if len(fns) == 1:
            fn = fns[0]
            extra = []
            if fn.get("matched_by"):
                extra.append(f"matched by {fn['matched_by']}")
            elif fn.get("best") is not None:
                extra.append(f"best {fn['best']} of {fn.get('attempts', 0)} tries")
            if fn.get("agent") and fn.get("status") == "working":
                extra.append(f"{fn['agent']} at work")
            where = f" cell {self.cursor + 1}/{len(self.map_cells)}"  # (counted from 1, as functions are)
            return (f"{where} · {fn['name']} · {mapgrid.status_of(fn)} · {fn.get('insns', 0)} insns"
                    + (" · " + " · ".join(extra) if extra else ""))
        counts = {s: sum(1 for fn in fns if mapgrid.status_of(fn) == s) for s in mapgrid.ORDER}
        said = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
        return (f" cell {self.cursor + 1}/{len(self.map_cells)} · {len(fns)} functions · {fns[0]['name']} – "
                f"{fns[-1]['name']} · {said} (on {chosen['name']})")

    def say(self, line: str) -> None:
        if self.is_mounted:
            self.query_one("#cell-line", Static).update(plain(f" {line}"))

    # -- keys (each does nothing until a map is there: a project not opened yet, or still on its way)

    def go(self, cell: int) -> None:
        """The cursor to `cell`, kept on the map."""
        if self.map_cells:
            self.cursor = min(max(cell, 0), len(self.map_cells) - 1)
            self.selected = self.map_cells[self.cursor].start
            self.note = None
            self.draw()

    def action_cursor_left(self) -> None:
        self.move_on_row(-1)

    def action_cursor_right(self) -> None:
        self.move_on_row(1)

    def action_cursor_up(self) -> None:
        self.move(-self.grid.cols)

    def action_cursor_down(self) -> None:
        self.move(self.grid.cols)

    def move(self, delta: int) -> None:
        """Up and down by a row: they stop at the map's top and bottom edges — the cursor stays where it is,
        rather than jumping to the map's first or last cell."""
        if self.map_cells and 0 <= self.cursor + delta < len(self.map_cells):
            self.go(self.cursor + delta)

    def move_on_row(self, delta: int) -> None:
        """Left and right stop at the row's ends (the last row's end is its last cell): they don't wrap."""
        if self.map_cells:
            row, col = divmod(self.cursor, self.grid.cols)
            self.go(row * self.grid.cols + min(max(col + delta, 0), self.grid.cols - 1))

    def action_row_start(self) -> None:
        self.go(self.cursor - self.cursor % self.grid.cols)

    def action_row_end(self) -> None:
        self.go((self.cursor // self.grid.cols + 1) * self.grid.cols - 1)

    def action_first(self) -> None:
        self.go(0)

    def action_last(self) -> None:
        self.go(len(self.map_cells) - 1)

    def width(self) -> int:
        return max(1, self.query_one("#map").size.width or FALLBACK_WIDTH) if self.is_mounted else FALLBACK_WIDTH

    def rows_free(self) -> int:
        """The lines the map has: every one the screen has left (mapgrid.ROWS before it's laid out)."""
        return max(1, self.query_one("#map").size.height or mapgrid.ROWS) if self.is_mounted else mapgrid.ROWS

    def action_open(self) -> None:
        if not self.map_cells:
            return
        held = self.map_cells[self.cursor]
        if len(held) == 1:
            self.app.push_screen(InfoPanel(self.rows[held.start]))
        else:
            self.app.push_screen(Picker([self.rows[i] for i in held]), self.picked)

    def picked(self, name: str | None) -> None:
        if name and (i := next((i for i, fn in enumerate(self.rows) if fn["name"] == name), None)) is not None:
            self.selected, self.note = i, None
            self.cursor = mapgrid.cell_of(self.map_cells, i)
            self.draw()
            self.app.push_screen(InfoPanel(self.rows[i]))

    def action_search(self) -> None:
        if self.rows:
            self.app.push_screen(Search(self.rows), self.searched)

    def searched(self, found: tuple[str, list[dict], int] | None) -> None:
        if not found:
            return
        needle, hits, picked = found
        names = [h["name"] for h in hits]
        where = {fn["name"]: i for i, fn in enumerate(self.rows)}
        indices = [where[n] for n in names if n in where]
        self.find = (needle, indices, picked - 1)
        self.action_again(1)  # onto the hit picked in the list

    def action_again(self, direction: int) -> None:
        if not self.map_cells:
            return
        if not self.find or not self.find[1]:
            self.say("nothing found yet — / searches names")
            return
        needle, hits, at = self.find
        at = (at + direction) % len(hits)
        self.find = (needle, hits, at)
        self.selected = hits[at]
        self.cursor = mapgrid.cell_of(self.map_cells, hits[at])
        self.note = f" “{needle}” {at + 1}/{len(hits)} · {self.rows[hits[at]]['name']} · n N for the others"
        self.draw()
