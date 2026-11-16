"""Screen 1: the projects, one row each — title, platform, a progress bar, matched over functions, the helpers
working on it now, and the progress error when a project can't be read (/api/projects, every 5 s while it's shown).
Enter opens the project's map."""

from __future__ import annotations

from textual.binding import Binding
from textual.widgets import DataTable, Static

from .app import Base, plain

POLL = 5


def code_share(p: dict) -> float:
    """The share of a project's code that's matched, as the window shows it (static/js/base.js, codePct): matched
    bytes over the code C can make — what's set aside is out of both sides."""
    code = max(0, int(p.get("bytes") or 0) - int(p.get("set_aside_bytes") or 0))
    return int(p.get("matched_bytes") or 0) / code if code else 0.0


def c_functions(p: dict) -> int:
    """The functions C can make, as the window counts them (static/js/base.js, cFunctions): set-aside ones left out."""
    return int(p.get("functions") or 0) - int(p.get("set_aside") or 0)


def percent(share: float) -> str:
    """As the window writes it (static/js/base.js, pctNum): two decimals under 10%, one above."""
    pct = 100 * share
    return f"{pct:.2f}%" if pct < 10 else f"{pct:.1f}%"


def progress_bar(share: float, width: int = 10) -> str:
    """How far the project got, as text: a bar and its share of the code matched."""
    filled = min(width, round(share * width))
    return f"{'█' * filled}{'░' * (width - filled)} {percent(share)}"


class ProjectsScreen(Base):
    """Every project, as the sidebar shows them (the home page of the TUI)."""

    BINDINGS = [Binding("enter", "open", "open the map", priority=True)]  # over the table's own Enter

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.projects: list[dict] = []
        self.sig: str | None = None  # the list's signature, once the app has said it (its poll: same, nothing new)

    def body(self) -> list:
        table = DataTable(id="projects", cursor_type="row", zebra_stripes=True)
        table.add_columns("project", "platform", "code matched", "functions", "helpers", "notes")
        return [Static("", id="no-projects"), table]

    def on_mount(self) -> None:
        self.set_interval(POLL, self.poll)

    def on_screen_resume(self) -> None:
        self.poll()  # shown (again): what changed meanwhile, at once

    def refresh_now(self) -> None:
        self.sig = None  # the whole list again, not the "nothing changed" of a poll
        self.poll(always=True)

    def poll(self, always: bool = False) -> None:
        if always or self.polling():
            self.fetch("projects", lambda: self.client.get("/api/projects", since=self.sig or ""), self.show)

    def show(self, data) -> None:
        if not isinstance(data, dict) or data.get("same"):
            return  # nothing changed, or nothing yet (the app is starting): the last list stays
        projects = data.get("projects")
        if not isinstance(projects, list):
            return
        self.sig, self.projects = data.get("sig"), projects
        table = self.query_one(DataTable)
        on = table.cursor_row if table.row_count else 0
        table.clear()
        for p in projects:
            note = p.get("progress_error") or p.get("broken") or ""
            table.add_row(plain(p.get("title") or p.get("slug")), plain(p.get("platform_short") or "?"),
                          progress_bar(code_share(p)), f"{int(p.get('matched') or 0)}/{c_functions(p)}",
                          str(p.get("working") or 0), plain(str(note)[:60]))
        if table.row_count:
            table.move_cursor(row=min(on, table.row_count - 1), animate=False)
        where = f" in {self.app.home}" if self.app.home else ""
        self.query_one("#no-projects", Static).update(plain(
            "" if projects else f" No projects{where} yet. Import a game in the browser (b shows a link to pair it)."))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """The table's own Enter (its binding outranks the screen's): a selected row opens like action_open."""
        self.action_open()

    def highlighted(self) -> dict | None:
        """The project under the cursor, once the list is there."""
        table = self.query_one(DataTable)
        return self.projects[table.cursor_row] if self.projects and table.cursor_row < len(self.projects) else None

    def action_open(self) -> None:
        p = self.highlighted()
        if p is None:
            return
        if p.get("broken"):  # no map to show: say why, here
            self.app.notify(str(p["broken"])[:300], title=f"{p.get('title') or p.get('slug')} can't be read",
                            severity="error", markup=False)
            return
        self.app.screens["map"].open_project(p.get("slug"))
        self.app.switch_screen("map")
