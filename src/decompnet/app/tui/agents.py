"""Screen 3: the agents — who works on what now (the claims /api/live brings), the board (/api/stats: compiles,
matches, a sparkline of matches per hour over the last day), and the feed: each try in the open project as it
happens (/api/live) and the notification centre's events from every project (/api/events), from when the screen
was first shown, newest at the bottom and following until you stop it (`f`). `L` reads the tail of decomp.log (on
request only: it can be big). It polls only while it's shown; the feed picks up where it left off.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.screen import ModalScreen
from textual.widgets import DataTable, Log, RichLog, Static

from .app import Base, plain

LIVE_POLL, STATS_POLL, EVENTS_POLL = 2, 10, 5
SPARK = "▁▂▃▄▅▆▇█"  # a sparkline's heights, low to high
FEED_LINES = 500  # the feed keeps this many; a terminal shows far fewer


def sparkline(per_hour: list) -> str:
    """24 hours of matches as one line of block characters (the window draws the same numbers as a chart)."""
    top = max(per_hour or [1])
    return "".join(SPARK[min(len(SPARK) - 1, v * len(SPARK) // (top or 1))] for v in (per_hour or []))


def how_long(claimed_at) -> str:
    """How long a claim has run ("14 m", "2 h 05 m"); "—" for one with no time (a room's claim)."""
    if not claimed_at:
        return "—"
    minutes = max(0, int((time.time() - claimed_at) / 60))
    return f"{minutes // 60} h {minutes % 60:02d} m" if minutes >= 60 else f"{minutes} m"


class LogView(ModalScreen):
    """`L`: the end of decomp.log, scrollable (the whole log is in the file; this is its last 512 KB)."""

    BINDINGS = [("escape", "dismiss", "close"), ("L", "dismiss", "close"), ("l", "dismiss", "close")]

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def action_dismiss(self) -> None:
        self.dismiss()

    def compose(self):
        yield Log(id="logview", highlight=False)  # plain text: a log line's [brackets] are not markup

    def on_mount(self) -> None:
        view = self.query_one(Log)
        view.write(self.text or "(nothing logged yet)")
        view.scroll_end(animate=False)


class AgentsScreen(Base):
    """The helpers at work: now, over the day, and as it happens."""

    BINDINGS = [("f", "follow", "follow the feed"), ("L", "log", "the log")]

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.follow = True
        self.slug: str | None = None
        self.last_attempt = 0
        self.last_event = 0  # /api/live sends its events after this one (the feed's come from /api/events)
        self.primed = False  # the first live answer of a project: where its attempts are up to, not news
        self.last_note: int | None = None  # the notification centre's feed: None until it said where it starts

    def body(self) -> list:
        working = DataTable(id="working", cursor_type="row", zebra_stripes=True)
        working.add_columns("agent", "function", "best", "tries", "for")
        board = DataTable(id="board", cursor_type="row", zebra_stripes=True)
        board.add_columns("agent", "compiles", "matches", "per hour (24 h)")
        return [Static(" working now", classes="heading"), working, Static(" the day", classes="heading"), board,
                Static(" the feed  (f follows, or stops at what you're reading)", classes="heading"),
                RichLog(id="feed", markup=False, wrap=True, max_lines=FEED_LINES)]

    def on_mount(self) -> None:
        self.set_interval(LIVE_POLL, self.poll_live)
        self.set_interval(STATS_POLL, self.poll_stats)
        self.set_interval(EVENTS_POLL, self.poll_events)
        self.poll_events(always=True)  # where the notification centre's feed starts: now

    def on_screen_resume(self) -> None:
        self.refresh_now()

    def refresh_now(self) -> None:
        self.poll_live(always=True)
        self.poll_stats(always=True)
        self.poll_events(always=True)

    def project(self) -> str | None:
        """The project the map is on: the one this screen shows (its feed starts again when it changes)."""
        slug = getattr(self.app.screens.get("map"), "slug", None)
        if slug != self.slug:
            self.slug, self.last_attempt, self.last_event, self.primed = slug, 0, 0, False
            if self.is_mounted:
                for table in self.query(DataTable):
                    table.clear()
        return slug

    # -- the three polls

    def poll_live(self, always: bool = False) -> None:
        if not (always or self.polling()) or (slug := self.project()) is None:
            return
        e, a = self.last_event, self.last_attempt  # (e: without it, every answer brings the last 60 events again)
        self.fetch("live", lambda: self.client.get("/api/live", slug=slug, e=e, a=a),
                   lambda d: self.took_live(slug, d))

    def took_live(self, slug: str, data) -> None:
        if slug != self.slug or not data:
            return
        table = self.query_one("#working", DataTable)
        on = table.cursor_row if table.row_count else 0
        table.clear()
        for c in data.get("claims") or []:
            best = "—" if c.get("best_score") is None else str(c["best_score"])
            table.add_row(plain(c.get("agent") or "?"), plain(c["name"]), best, str(c.get("attempts") or 0),
                          how_long(c.get("claimed_at")))
        if table.row_count:
            table.move_cursor(row=min(on, table.row_count - 1), animate=False)
        attempts = data.get("attempts") or []
        if self.primed:
            for at in attempts:
                score = "didn't compile" if not at.get("compiled") else f"score {at.get('score')}"
                self.feeds(f" {at.get('agent') or '?'} tried {at['function']} · {score}")
        self.primed = True
        if attempts:
            self.last_attempt = attempts[-1]["id"]
        if data.get("events"):
            self.last_event = data["events"][-1]["id"]

    def poll_stats(self, always: bool = False) -> None:
        if not (always or self.polling()) or (slug := self.project()) is None:
            return
        self.fetch("stats", lambda: self.client.get("/api/stats", slug=slug), lambda d: self.took_stats(slug, d))

    def took_stats(self, slug: str, stats) -> None:
        if slug != self.slug or not stats:
            return
        table = self.query_one("#board", DataTable)
        on = table.cursor_row if table.row_count else 0
        table.clear()
        for row in stats.get("agents") or []:
            table.add_row(plain(row.get("agent") or "?"), str(row.get("compiles") or 0), str(row.get("matched") or 0),
                          Text(sparkline(stats.get("matches_per_hour"))))
        if table.row_count:
            table.move_cursor(row=min(on, table.row_count - 1), animate=False)

    def poll_events(self, always: bool = False) -> None:
        """The notification centre's feed, across every project (as the window's bell shows it). Until the app has
        said where the feed starts (nothing that happened before is news), each poll asks that again: the first ask
        comes as `decomp serve` starts, and the app may not answer it yet."""
        if not (always or self.polling()):
            return
        if self.last_note is None:
            self.fetch("events", lambda: self.client.get("/api/events"), self.took_events)
            return
        after = self.last_note
        self.fetch("events", lambda: self.client.get("/api/events", after=after), self.took_events)

    def took_events(self, data) -> None:
        if not isinstance(data, dict):
            return
        for event in data.get("events") or []:
            where = f"{event.get('project')}: " if event.get("project") else ""
            self.feeds(f" · {where}{event.get('kind')}: {event.get('detail')}")
        if isinstance(data.get("last"), int):
            self.last_note = data["last"]

    def feeds(self, line: str) -> None:
        feed = self.query_one("#feed", RichLog)
        feed.write(plain(line), shrink=True, scroll_end=self.follow)

    # -- keys

    def action_follow(self) -> None:
        self.follow = not self.follow
        if self.follow:
            self.query_one("#feed", RichLog).scroll_end(animate=False)

    def action_log(self) -> None:
        self.fetch("log", lambda: self.client.text("/api/log"), lambda text: self.app.push_screen(LogView(text or "")))
