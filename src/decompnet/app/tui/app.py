"""The Textual App: the three screens (projects.py, mapview.py, agents.py), the keys that work everywhere, and the
colours. It holds one Client of the app it runs beside (cli.py started both) and decomp serve's one-time links
(sessions.py): `b` shows a fresh one for the browser.
"""

from __future__ import annotations

import logging
import os
import traceback

from rich.text import Text
from textual.app import App
from textual.binding import Binding
from textual.screen import ModalScreen, Screen
from textual.widget import Widget
from textual.widgets import Footer, Static
from textual.worker import get_current_worker

from . import colors

log = logging.getLogger("decompdecomp.tui")


def full_colour(env=None) -> bool:
    """Whether the terminal says it shows more than the 8 or 16 basic colours: COLORTERM (truecolor, 24bit) or a
    TERM with 256color or direct in it. Anything else (TERM=linux, screen, vt100...) gets one basic colour per
    status: shades a terminal can't show come out as the same grey."""
    env = os.environ if env is None else env
    term = env.get("TERM", "")
    return env.get("COLORTERM", "") in ("truecolor", "24bit") or "256color" in term or "direct" in term


def palette(dark: bool, full: bool) -> dict[str, str]:
    """A colour per map status for this terminal: the window's own (colors.py; light or dark as the TUI's theme is)
    where it shows them, the console's one-basic-colour-per-status where it doesn't."""
    return colors.window_palette(dark) if full else dict(colors.CONSOLE)


def plain(text: object) -> Text:
    """Text shown as it is: a function's name or a log line can hold [brackets] that Textual would take for markup."""
    return Text(str(text))


class TopBar(Static):
    """The one line above everything: the project the map is on, and where a browser reaches the same app."""

    def on_mount(self) -> None:
        self.show()

    def show(self) -> None:
        app: DecompApp = self.app
        self.update(plain(f" {app.edition} · {app.project_name or 'no project open yet'} · browser: "
                          f"{app.browser_address} (b pairs it)"))


class Base(Screen):
    """Every screen's frame: the top bar, the screen's own body, the keys (Textual's Footer, from the bindings) —
    and fetch(), how every screen asks the app: in a thread, so a slow answer never freezes the terminal."""

    def __init__(self) -> None:
        super().__init__()
        self._asking: set[str] = set()  # the fetches under way, by group: a poll doesn't start beside its last one

    def compose(self):
        yield TopBar()
        yield from self.body()
        yield Footer()

    def body(self) -> list[Widget]:
        return [Static("")]

    def refresh_now(self) -> None:
        """`r`: everything this screen shows, again."""

    def polling(self) -> bool:
        """Whether this screen's polls should ask now: only the screen being looked at polls — the one on top, or
        the one under a box shown over it (the browser link `decomp serve` starts on, a panel, the log): Textual's
        is_current counts a screen still visible around a ModalScreen."""
        return self.is_mounted and self.is_current

    def fetch(self, group: str, ask, apply) -> None:
        """ask() in a thread, then apply(answer) on the screen. One fetch per group at a time (a poll whose last ask
        is still out skips its turn). An error in either is logged and kept in app.problems, and the TUI goes on:
        its next poll may well work."""
        if group in self._asking:
            return
        self._asking.add(group)

        def work() -> None:
            try:
                answer = ask()
                if not get_current_worker().is_cancelled:
                    self.app.call_from_thread(apply, answer)
            except RuntimeError:  # the app ended while the answer was on its way
                pass
            except Exception:
                self.app.call_from_thread(self.app.problem, traceback.format_exc())
            finally:
                self._asking.discard(group)

        self.run_worker(work, group=group, thread=True, exit_on_error=False)


class AskStop(ModalScreen):
    """`q`, Ctrl-C and Ctrl-Q: leaving the TUI ends the app and every helper with it (cli.run's quit_app), so it
    asks."""

    BINDINGS = [("escape", "dismiss", "leave it"), ("n", "dismiss", "leave it"),
                ("y", "stop", "stop everything"), ("enter", "stop", "stop everything")]

    def action_stop(self) -> None:
        self.app.exit("stop")

    def action_dismiss(self) -> None:
        self.dismiss()

    def compose(self):
        yield Static("  Stop decompdecomp and its agents?\n\n"
                     "  y stops everything; n leaves it running\n"
                     "  (to leave it running, detach from screen or tmux instead, Ctrl-A d / Ctrl-B d,\n"
                     "   and log out: screen -r or tmux attach brings the TUI back)", id="ask", markup=False)
        yield Footer()


class BrowserLink(ModalScreen):
    """`b`: a fresh one-time link for the browser, from decomp serve itself (sessions.py). Select it with the mouse to
    copy it, as anywhere in the terminal (the TUI leaves the mouse to it: tui.run). `c` copies it too, for a terminal
    that takes OSC 52 (the clipboard of the computer the terminal is on, over SSH too; tmux passes it on when set to,
    GNU screen doesn't)."""

    BINDINGS = [("escape", "dismiss", "close"), ("c", "copy", "copy it"), ("b", "link", "a fresh one"),
                ("enter", "dismiss", "close")]

    def __init__(self) -> None:
        super().__init__()
        self.link = ""

    def action_dismiss(self) -> None:
        self.dismiss()

    def action_link(self) -> None:
        self.query_one("#link", Static).update(self.text())

    def action_copy(self) -> None:
        self.app.copy_to_clipboard(self.link)
        self.query_one("#link", Static).update(self.text(fresh=False, said="  Copied to your clipboard (if nothing "
                                                         "pastes: see Copying in docs/TUI.md)."))

    def text(self, fresh: bool = True, said: str = "") -> Text:
        if fresh or not self.link:
            self.link = self.app.fresh_link()
        return plain(f"  Pair a browser: open this link in it, through the SSH tunnel\n"
                     "  (if your end of the tunnel isn't this port, change the port in the link to yours)\n\n"
                     f"  {self.link}\n\n"
                     "  It works once, within 15 minutes.\n"
                     "  c copies it · b shows a fresh one · Escape closes this"
                     + (f"\n\n{said}" if said else ""))

    def compose(self):
        yield Static(self.text(), id="link")
        yield Footer()


class ShowKeys(ModalScreen):
    BINDINGS = [("escape", "dismiss", "close"), ("?", "dismiss", "close")]

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def action_dismiss(self) -> None:
        self.dismiss()

    def compose(self):
        yield Static(self.text, id="keys", markup=False)
        yield Footer()


HELP = "\n".join([
    "  everywhere   1 2 3 screens · Tab next · b browser link · r refresh · q stop · ? this list",
    "               the mouse is your terminal's: select and copy as anywhere · Ctrl-P commands (a light theme)",
    "  projects     Enter opens the project's map",
    "  map          ← ↓ ↑ → h j k l move · Home End the row · g G first/last cell · Enter the cell's function(s)",
    "               / search · n N next and previous find",
    "  agents       f follow the feed on and off · L the log's tail",
])


class DecompApp(App):
    """decompdecomp in a terminal: read-only in this first version."""

    TITLE = "decompdecomp"
    CSS = """
    TopBar { background: $panel; color: $text; height: 1; text-style: bold; }
    #ask, #link, #keys, #info, #picker-hint { padding: 1 2; border: round $primary; }
    #ask, #link, #keys, #info { width: auto; height: auto; }
    #logview { height: 1fr; width: 1fr; border: round $primary; }
    .heading { color: $text-muted; text-style: bold; height: 1; }
    .maparea { height: 1fr; overflow: hidden; }  /* the map: every row the screen has left, as wide as it */
    /* a picker's or a search's list: what's left of the screen under the box above it, so it scrolls inside that,
       and the highlighted line never sits below the bottom (as tall as the screen, it ran past it) */
    Picker > OptionList, Search > OptionList { height: 1fr; }
    #cell-line { height: 1; color: $text; }
    #no-projects { height: auto; color: $text-muted; }
    #overview-line, #legend-line { height: 1; color: $text-muted; }
    #working { height: 5; } #board { height: 6; } #feed { border: round $primary; }
    """
    BINDINGS = [
        ("1", "screen('projects')", "Projects"),
        ("2", "screen('map')", "Map"),
        ("3", "screen('agents')", "Agents"),
        Binding("tab", "next_screen", "next", priority=True),  # Tab walks the screens, not the widgets
        ("b", "link", "browser link"),
        ("r", "refresh", "refresh"),
        ("q", "ask", "stop"),
        Binding("ctrl+c", "ask", "stop", show=False, priority=True),
        Binding("ctrl+q", "ask", "stop", show=False, priority=True),  # (Textual's own quits without asking)
        ("?", "keys", "keys"),
    ]
    ORDER = ("projects", "map", "agents")  # what Tab walks through

    def __init__(self, client, new_code, name: str = "decompdecomp", home: str = "", show_link: bool = False):
        super().__init__()
        self.client = client
        self.new_code = new_code
        self.edition, self.home = name, home  # which decompdecomp this is, and where its projects are (edition.py)
        self.show_link = show_link  # `decomp serve` starting: the browser link first, as the first thing to do
        self.project_name = ""
        self.browser_address = client.base
        self.screens: dict[str, Base] = {}
        self.problems: list[str] = []  # errors the screens caught and carried on from (logged; tests check it's empty)
        self.full = full_colour()

    def problem(self, text: str) -> None:
        self.problems.append(text)
        log.error("the TUI carried on after an error:\n%s", text)

    def colours(self) -> dict[str, str]:
        """The map's colours for this terminal and the TUI's theme now (Ctrl-P can change it)."""
        return palette(bool(getattr(self.current_theme, "dark", True)), self.full)

    def fresh_link(self) -> str:
        return f"{self.client.base}/pair?code={self.new_code()}"

    def on_mount(self) -> None:
        from . import agents, mapview, projects  # at run time: they import this module for Base and the palette

        self.screens = {"projects": projects.ProjectsScreen(self.client),
                        "map": mapview.MapScreen(self.client),
                        "agents": agents.AgentsScreen(self.client)}
        for name, screen in self.screens.items():
            self.install_screen(screen, name=name)
        # pushed, not switched: Textual 8 can't switch away from its default first screen (it pops an empty stack)
        self.push_screen("projects")
        if self.show_link:
            self.push_screen(BrowserLink())

    def say_project(self, name: str) -> None:
        """The top bar names the project the map is on (mapview, when a map arrives)."""
        self.project_name = name
        for screen in self.screens.values():
            if screen.is_mounted:
                for bar in screen.query(TopBar):
                    bar.show()

    def action_screen(self, name: str) -> None:
        if self.screen is not self.screens.get(name):
            self.switch_screen(name)

    def action_next_screen(self) -> None:
        current = next((name for name in self.ORDER if self.screens.get(name) is self.screen), self.ORDER[0])
        self.switch_screen(self.ORDER[(self.ORDER.index(current) + 1) % len(self.ORDER)])

    def action_link(self) -> None:
        self.push_screen(BrowserLink())

    def action_ask(self) -> None:
        """`q`, Ctrl-C, Ctrl-Q: ask before stopping everything."""
        if not isinstance(self.screen, AskStop):
            self.push_screen(AskStop())

    def action_keys(self) -> None:
        self.push_screen(ShowKeys(HELP))

    def action_refresh(self) -> None:
        self.screen.refresh_now() if isinstance(self.screen, Base) else None
