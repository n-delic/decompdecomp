"""The TUI against the real app: a project seeded straight into the database (no compiler, no game: it never skips),
the app's own server on a free port, and the TUI's own Client asking it — the same requests `decomp serve` makes.

This is what keeps the TUI working while the API changes. test_every_field_the_tui_reads_is_in_the_apps_answers
checks each field tui/client.py's READS names, so a change to a route the TUI reads fails with the route and field
named; the screen tests below drive the TUI with Textual's pilot and check what it shows from that seeded project.
A change to any other route doesn't concern these tests.
"""

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from decompnet.app import cli, tui
from decompnet.app.engine import RomNetwork
from decompnet.app.server import build
from decompnet.app.tui import colors, mapgrid
from decompnet.app.tui.app import DecompApp, full_colour
from decompnet.app.tui.client import MAP_STATUSES, READS, Client
from decompnet.app.tui.projects import percent

SECRET = "tui-test-secret"
SLUG = "tui-seed"
CLAIMED, TRIED = "func_80000020", "func_80000030"
LOG_LINE = "a log line with [bold]brackets[/] that aren't markup"
# the seeded functions, in the map's order, and what the TUI shows each as
SEEDED = [("func_80000000", "polished"), ("func_80000010", "matched"), (CLAIMED, "in progress"), (TRIED, "tried"),
          ("func_80000040", "not C"), ("func_80000050", "not C"), ("func_80000060", "open"), ("func_80000070", "open")]
ASKS = {"/api/map": {"slug": SLUG}, "/api/live": {"slug": SLUG}, "/api/stats": {"slug": SLUG},
        "/api/events": {"after": 0}, "/api/projects": {"since": ""}, "/api/log": {}}


def seed(net: RomNetwork, home: Path) -> None:
    """A project with a function in every status the map shows, a claim, attempts, an event, and one project that
    can't be read. Straight into the database: the API answers it as it would a real import's."""
    (home / "tui-root").mkdir(parents=True, exist_ok=True)
    with net.store.tx() as db:
        db.execute("INSERT INTO agents(id, name, owner, token_hash) VALUES(1, 'claude-3', 'local', 'seed-1')")
        db.execute("INSERT INTO projects(id, slug, name, arch, root, config) VALUES(1, ?, 'TUI Seed', 'mips', ?, ?)",
                   (SLUG, str(home / "tui-root"), json.dumps(
                       {"kind": "rom", "platform": "n64",
                        "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": []},
                        "rom": {"title": "TUI Seed", "game_code": "TSED", "region": "E", "size": 1048576,
                                "sha1": "0" * 40, "byte_order": "big"}})))
        db.execute("INSERT INTO projects(id, slug, name, arch, root, config) VALUES(2, 'tui-broken', 'Broken', "
                   "'mips', ?, '{}')", (str(home / "gone"),))
        rows = [  # name, insns, status, best, attempts, matched_by, matched_at, readable, meta
            ("func_80000000", 132, "matched", 0, 4, 1, 1700000000.0, 1, None),
            ("func_80000010", 12, "matched", 0, 7, 1, 1700000100.0, 0, None),
            (CLAIMED, 64, "open", 700, 1, None, None, 0, None),
            (TRIED, 28, "open", 400, 2, None, None, 0, None),
            ("func_80000040", 220, "asm", None, 9, None, None, 0, '{"not_c": true}'),
            ("func_80000050", 44, "asm", None, 1, None, None, 0, None),
            ("func_80000060", 8, "open", None, 0, None, None, 0, None),
            ("func_80000070", 500, "open", None, 0, None, None, 0, None)]
        for i, (name, insns, status, best, tries, by, at, readable, meta) in enumerate(rows):
            db.execute("INSERT INTO functions(id, project_id, name, vram, size, n_insns, asm, relocs, status, "
                       "best_score, attempts, unit, matched_agent_id, matched_at, readable, meta) "
                       "VALUES(?, 1, ?, ?, ?, ?, 'jr $ra', '[]', ?, ?, ?, ?, ?, ?, ?, ?)",
                       (i + 1, name, 0x80000000 + 16 * i, insns * 4, insns, status, best, tries,
                        "main.c" if i % 2 else "math.c", by, at, readable, meta))
        db.execute("INSERT INTO claims(function_id, agent_id, claimed_at, expires_at) VALUES(3, 1, ?, 4102444800.0)",
                   (time.time() - 600,))
        db.execute("INSERT INTO context_versions(project_id, version, header, message, created_at) "
                   "VALUES(1, 1, '', 'seeded', 0)")  # an import writes one; the overview reads its version
        db.execute("INSERT INTO attempts(id, function_id, agent_id, score, compiled, created_at) VALUES"
                   "(1, 4, 1, 400, 1, 1700000300.0), (2, 4, 1, 900, 1, 1700000400.0)")
        db.execute("INSERT INTO events(id, project_id, agent_id, kind, detail, created_at) VALUES"
                   "(1, 1, 1, 'match', 'func_80000010 matched', 1700000500.0)")


class Live:
    """The app, seeded and serving on a free port of 127.0.0.1, and the database to change under it."""

    def __init__(self, tmp: Path):
        home = tmp / "home"
        (home / ".decomp" / "logs").mkdir(parents=True)
        self.net = RomNetwork(home / ".decomp" / "decomp.db", home)
        seed(self.net, home)
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            self.port = free.getsockname()[1]
        mcp, _, security, _ = build(self.net, home / ".decomp", self.port, SECRET)
        (home / ".decomp" / "logs" / "decomp.log").open("a", encoding="utf-8").write(LOG_LINE + "\n")
        app = mcp.streamable_http_app(transport_security=security, host="127.0.0.1")
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.05)
        self.client = Client(self.port, SECRET)

    def sql(self, statement: str, *args) -> None:
        with self.net.store.tx() as db:
            db.execute(statement, args)

    def stop(self) -> None:
        self.server.should_exit = True


@pytest.fixture
def live(tmp_path):
    app = Live(tmp_path)
    yield app
    app.stop()


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def settle(pilot, rounds: int = 3) -> None:
    """Until the TUI's asks are answered and drawn (they run in threads: fetch in tui/app.py)."""
    for _ in range(rounds):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()


def tui_on(live: Live, size=(100, 30)):
    return DecompApp(live.client, lambda: "a-fresh-code").run_test(size=size)


async def open_map(pilot):
    """The seeded project's map, opened the way a user does: its row on screen 1, Enter."""
    await settle(pilot)
    await pilot.press("down", "enter")  # the readable project is the second row
    await settle(pilot)
    return pilot.app.screens["map"]


def fields_of(value, path: str) -> list:
    """The values at `path` in an answer ("rows[].name": each row's name; "" the answer itself)."""
    found = [value]
    for part in [p for p in path.split(".") if p]:
        many = part.endswith("[]")
        key = part.removesuffix("[]")
        found = [v[key] for v in found if isinstance(v, dict) and key in v]
        if many:
            found = [item for v in found if isinstance(v, list) for item in v]
    return found


JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def test_every_field_the_tui_reads_is_in_the_apps_answers(live):
    """The TUI's contract with the API (tui/client.py, READS): each field there, in the real app's answers, with
    that type (or null, where an answer has nothing to say). A field renamed, removed or retyped fails here."""
    problems = []
    for route, reads in READS.items():
        if reads == {"": "text"}:
            if live.client.text(route, **ASKS[route]) is None:
                problems.append(f"{route} doesn't answer")
            continue
        answer = live.client.get(route, **ASKS[route])
        if route == "/api/map":  # rows go as arrays under `keys`: read by those names, as the TUI does
            answer = dict(answer, rows=[dict(zip(answer["keys"], row)) for row in answer["rows"]])
        sig = answer.get("sig") if isinstance(answer, dict) else None
        if sig and route in ("/api/map", "/api/projects"):  # the poll's other answer: nothing changed
            again = live.client.get(route, **{**ASKS[route], "since": sig})
            answer = {**answer, "same": again.get("same")}
        for path, kind in reads.items():
            values = fields_of(answer, path)
            if not values:
                problems.append(f"{route}: no {path} in the answer")
            elif any(v is not None and (not isinstance(v, JSON_TYPES[kind])
                                        or (kind != "boolean" and isinstance(v, bool))) for v in values):
                problems.append(f"{route}: {path} isn't {kind} ({values[:3]})")
            elif all(v is None for v in values) and path not in ("projects[].progress_error",):
                problems.append(f"{route}: {path} is null everywhere: "
                                "the seed (tests/test_tui.py) should give it a value")
    answer = live.client.get("/api/map", slug=SLUG)
    statuses = {dict(zip(answer["keys"], row))["status"] for row in answer["rows"]}
    if statuses - MAP_STATUSES:
        problems.append(f"/api/map has statuses the TUI doesn't paint: {sorted(statuses - MAP_STATUSES)} "
                        "(teach mapgrid.status_of and client.MAP_STATUSES)")
    assert not problems, "the API changed under the TUI (src/decompnet/app/tui, docs/TUI.md):\n" + "\n".join(problems)


@pytest.mark.anyio
async def test_projects_list_and_the_map_of_one(live):
    async with tui_on(live) as pilot:
        app = pilot.app
        map_screen = await open_map(pilot)
        assert type(app.screen).__name__ == "MapScreen"
        assert [p["slug"] for p in app.screens["projects"].projects] == ["tui-broken", SLUG]
        assert [(fn["name"], mapgrid.status_of(fn)) for fn in map_screen.rows] == SEEDED
        assert app.project_name == "TUI Seed"
        assert "8 functions · 2 matched" in map_screen.overview_line
        assert "polished" in str(map_screen.query_one("#cell-line").content)
        assert not app.problems


@pytest.mark.anyio
async def test_the_three_screens_switch_and_only_the_one_shown_polls(live):
    async with tui_on(live) as pilot:
        app = pilot.app
        await open_map(pilot)
        for key, name in (("3", "AgentsScreen"), ("tab", "ProjectsScreen"), ("2", "MapScreen"),
                          ("1", "ProjectsScreen")):
            await pilot.press(key)
            await settle(pilot)
            assert type(app.screen).__name__ == name
        assert not app.screens["map"].polling() and not app.screens["agents"].polling()
        assert app.screens["projects"].polling()


@pytest.mark.anyio
async def test_keys_before_a_map_is_there_do_nothing(live):
    async with tui_on(live) as pilot:
        await pilot.press("2")
        for key in ("g", "G", "home", "end", "left", "right", "up", "down", "enter", "n", "N", "/"):
            await pilot.press(key)
        await settle(pilot)
        assert pilot.app.is_running and type(pilot.app.screen).__name__ == "MapScreen"


@pytest.mark.anyio
@pytest.mark.parametrize("size", [(5, 7), (45, 11)])  # one character a cell; blocks of 9 × 3
async def test_the_cursor_stays_on_the_map_of_a_short_last_row(live, size):
    async with tui_on(live, size=size) as pilot:  # 8 functions, 5 to a row: a row of 5 and a row of 3
        map_screen = await open_map(pilot)
        assert map_screen.grid.cols == 5
        await pilot.press("G")
        assert map_screen.cursor == 7
        await pilot.press("right", "end")
        assert map_screen.cursor == 7
        await pilot.press("g", "end", "down")  # under the first row's end there's no cell: it stays
        assert map_screen.cursor == 4
        await pilot.press("left", "left", "down")  # under the third cell: the last row's third
        assert map_screen.cursor == 7
        assert "cell 8/8" in str(map_screen.query_one("#cell-line").content)  # counted from 1, as functions are
        assert pilot.app.is_running


@pytest.mark.anyio
async def test_a_small_game_fills_the_screen_in_blocks(live):
    """95 functions on a 100 × 41 terminal (36 rows free): blocks of 9 × 4, 11 to a row, every row used — not 95
    characters on one line."""
    add_functions(live, 87)
    async with tui_on(live, size=(100, 41)) as pilot:
        map_screen = await open_map(pilot)
        grid = map_screen.grid
        assert (grid.cols, grid.bw, grid.bh, len(grid.cells)) == (11, 9, 4, 95)
        lines = map_screen.query_one("#map").content.plain.split("\n")
        assert len(lines) == 36 and max(len(line) for line in lines) <= 100
        await pilot.press("down")  # a row down is 11 functions on
        assert map_screen.cursor == 11 and "cell 12/95" in str(map_screen.query_one("#cell-line").content)


@pytest.mark.anyio
async def test_attempts_and_claims_come_onto_the_map_once(live):
    """The first live answer's attempts are in the map already (they're not counted again); a new attempt is; a
    claim that ends leaves its function open, as the window shows it."""
    async with tui_on(live) as pilot:
        map_screen = await open_map(pilot)
        tried = lambda: next(fn for fn in map_screen.rows if fn["name"] == TRIED)  # noqa: E731
        map_screen.poll_live(always=True)
        await settle(pilot)
        assert tried()["attempts"] == 2
        live.sql("INSERT INTO attempts(id, function_id, agent_id, score, compiled, created_at) "
                 "VALUES(3, 4, 1, 120, 1, ?)", time.time())
        live.sql("DELETE FROM claims")
        map_screen.poll_live(always=True)
        await settle(pilot)
        assert tried()["attempts"] == 3 and tried()["best"] == 120
        claimed = next(fn for fn in map_screen.rows if fn["name"] == CLAIMED)
        assert mapgrid.status_of(claimed) == "tried" and claimed["agent"] is None  # (it has a best: tried)


@pytest.mark.anyio
async def test_names_with_brackets_are_shown_as_they_are(live):
    """A C++ name holds [brackets], which Textual would read as markup: everywhere a name shows, it shows as is."""
    live.sql("UPDATE functions SET name='std::vector<int>::operator[]' WHERE id=1")
    live.sql("UPDATE functions SET name='close[/b]' WHERE id=2")
    async with tui_on(live) as pilot:
        map_screen = await open_map(pilot)
        await pilot.press("right")
        assert "close[/b]" in str(map_screen.query_one("#cell-line").content)
        await pilot.press("enter")  # its info panel
        await settle(pilot)
        assert type(pilot.app.screen).__name__ == "InfoPanel"
        await pilot.press("escape", "/", *"[")  # and the search lists both
        await settle(pilot)
        assert [h["name"] for h in pilot.app.screen.hits] == ["std::vector<int>::operator[]", "close[/b]"]
        await pilot.press("escape", "3")
        await settle(pilot)
        assert not pilot.app.problems and pilot.app.is_running


@pytest.mark.anyio
async def test_search_goes_to_the_hit_picked_and_n_steps_on(live):
    async with tui_on(live) as pilot:
        map_screen = await open_map(pilot)
        await pilot.press("/", *"FUNC_8000")  # case doesn't matter
        await settle(pilot)
        await pilot.press("down", "down", "enter")  # the third hit
        await settle(pilot)
        assert map_screen.rows[map_screen.selected]["name"] == CLAIMED
        assert "3/8" in str(map_screen.query_one("#cell-line").content)  # (and the line says so, not the cell)
        await pilot.press("n")
        assert map_screen.rows[map_screen.selected]["name"] == TRIED
        await pilot.press("N", "N")
        assert map_screen.rows[map_screen.selected]["name"] == "func_80000010"


def add_functions(live: Live, n: int) -> None:
    """`n` more open functions in the seeded project, after its eight."""
    with live.net.store.tx() as db:
        db.executemany("INSERT INTO functions(id, project_id, name, vram, size, n_insns, asm, relocs, status, "
                       "attempts) VALUES(?, 1, ?, ?, 4, 1, '', '[]', 'open', 0)",
                       [(i, f"func_{0x80001000 + 16 * i:08X}", 0x80001000 + 16 * i) for i in range(9, 9 + n)])


@pytest.mark.anyio
async def test_the_picker_for_a_cell_of_several_functions(live):
    add_functions(live, 3000)  # on 40 columns and the 7 rows a 12-line screen leaves: about eleven to a cell
    async with tui_on(live, size=(40, 12)) as pilot:
        map_screen = await open_map(pilot)
        held = len(map_screen.map_cells[0])
        assert held > 5
        await pilot.press("enter")
        assert type(pilot.app.screen).__name__ == "Picker"
        await settle(pilot)
        listed = pilot.app.screen.query_one("OptionList")
        for _ in range(held):  # to the bottom of a list longer than the screen has room for
            await pilot.press("down")
        await settle(pilot)
        assert listed.highlighted == held - 1
        assert listed.region.bottom <= pilot.app.screen.size.height  # the list ends on the screen...
        top, shown = listed.scroll_offset.y, listed.scrollable_content_region.height
        # (where the highlighted option's lines are: one can wrap onto two)
        line, tall = listed._index_to_line[listed.highlighted], listed._heights[listed.highlighted]
        assert top <= line and line + tall <= top + shown  # ...and shows its highlight
        await pilot.press(*"0010", "enter")  # typing filters; Enter shows the one left
        await settle(pilot)
        assert type(pilot.app.screen).__name__ == "InfoPanel"
        await pilot.press("escape")
        assert pilot.app.screen is map_screen and map_screen.rows[map_screen.selected]["name"] == "func_80000010"


@pytest.mark.anyio
async def test_the_map_uses_every_row_the_screen_has(live):
    """The map fills what the screen has left under the top line and over the four lines below it, and follows a
    resize, the cursor staying on its function."""
    add_functions(live, 20000)
    async with tui_on(live, size=(100, 41)) as pilot:
        map_screen = await open_map(pilot)
        assert map_screen.rows_free() == 36 and len(map_screen.map_cells) == 3600
        await pilot.press("G")
        on = map_screen.selected
        await pilot.resize_terminal(80, 25)
        await settle(pilot)
        assert map_screen.rows_free() == 20 and len(map_screen.map_cells) == 1600
        assert on in map_screen.map_cells[map_screen.cursor]


@pytest.mark.anyio
async def test_the_agents_screen_its_feed_and_the_log(live):
    async with tui_on(live) as pilot:
        await open_map(pilot)
        await pilot.press("3")
        await settle(pilot)
        agents = pilot.app.screens["agents"]
        working = agents.query_one("#working")
        assert working.row_count == 1 and str(working.get_row_at(0)[1]) == CLAIMED
        assert agents.query_one("#board").row_count == 1
        feed = agents.query_one("#feed")
        assert len(feed.lines) == 0  # what happened before isn't news
        live.sql("INSERT INTO events(id, project_id, agent_id, kind, detail, created_at) VALUES"
                 "(2, 1, 1, 'match', 'func_80000060 matched', ?)", time.time())
        live.sql("INSERT INTO attempts(id, function_id, agent_id, score, compiled, created_at) "
                 "VALUES(3, 7, 1, 50, 1, ?)", time.time())
        await pilot.press("r")
        await settle(pilot)
        said = "\n".join(line.text for line in feed.lines)
        assert "func_80000060 matched" in said and "tried func_80000060 · score 50" in said
        assert said.count("func_80000060 matched") == 1
        await pilot.press("L")
        await settle(pilot)
        assert type(pilot.app.screen).__name__ == "LogView"
        assert LOG_LINE in "\n".join(pilot.app.screen.query_one("#logview").lines)
        assert not pilot.app.problems


@pytest.mark.anyio
async def test_the_events_feed_starts_when_its_first_ask_failed(live):
    """The agents screen asks where the notification centre's feed starts when it's first shown, which can be as
    `decomp serve` starts, before the app answers: an ask that failed is asked again, the feed isn't off for good."""
    real, down = live.client.get, [True]

    def get(path, **params):
        if path == "/api/events" and down[0]:
            return None  # what Client.get gives for an app that doesn't answer
        return real(path, **params)

    live.client.get = get
    async with tui_on(live) as pilot:
        await open_map(pilot)
        await pilot.press("3")
        await settle(pilot)
        agents = pilot.app.screens["agents"]
        assert agents.last_note is None
        down[0] = False
        await pilot.press("r")
        await settle(pilot)
        assert agents.last_note is not None
        live.sql("INSERT INTO events(id, project_id, agent_id, kind, detail, created_at) VALUES"
                 "(2, 1, 1, 'match', 'func_80000060 matched', ?)", time.time())
        await pilot.press("r")
        await settle(pilot)
        assert "func_80000060 matched" in "\n".join(line.text for line in agents.query_one("#feed").lines)
        assert not pilot.app.problems


@pytest.mark.anyio
async def test_the_screen_under_a_box_keeps_polling(live):
    """`decomp serve` starts on the browser link, shown over screen 1: the projects fill in behind it, not only once
    it's closed. A box over a screen (the link, a panel, the log) leaves that screen polling; another screen doesn't."""
    async with DecompApp(live.client, lambda: "a-fresh-code", show_link=True).run_test(size=(100, 30)) as pilot:
        await settle(pilot)
        app = pilot.app
        assert type(app.screen).__name__ == "BrowserLink"
        assert app.screens["projects"].polling() and not app.screens["map"].polling()
        assert app.screens["projects"].query_one("#projects").row_count == 2


@pytest.mark.anyio
async def test_b_shows_a_one_time_browser_link_and_c_copies_it(live):
    """`b` shows a fresh one-time link; `c` puts the one shown on the clipboard (OSC 52), beside the terminal's own
    selection (the TUI leaves the mouse to it: the test after this one)."""
    codes = iter(["first-code", "second-code"])
    async with DecompApp(live.client, lambda: next(codes)).run_test(size=(100, 30)) as pilot:
        await pilot.press("b")
        assert type(pilot.app.screen).__name__ == "BrowserLink"
        assert "/pair?code=first-code" in str(pilot.app.screen.query_one("#link").content)
        await pilot.press("c")
        assert pilot.app.clipboard == f"{live.client.base}/pair?code=first-code"  # the link shown, not a new one
        assert "Copied" in str(pilot.app.screen.query_one("#link").content)
        await pilot.press("b", "c")
        assert pilot.app.clipboard.endswith("second-code")


def test_the_tui_leaves_the_mouse_to_the_terminal(monkeypatch):
    """No mouse: the terminal's own selection copies the link (or anything), inside screen and tmux too."""
    asked = {}
    monkeypatch.setattr(tui.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(tui.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(DecompApp, "run", lambda self, **how: asked.update(how))
    tui.run(1, SECRET, lambda: "code")
    assert asked == {"mouse": False}


@pytest.mark.anyio
async def test_q_ctrl_c_and_ctrl_q_ask_before_they_stop_everything(live):
    async with tui_on(live) as pilot:
        app = pilot.app
        for key in ("q", "ctrl+c", "ctrl+q"):
            await pilot.press(key)
            assert type(app.screen).__name__ == "AskStop"
            await pilot.press("n")
            assert app.is_running and type(app.screen).__name__ == "ProjectsScreen"
        await pilot.press("q", "y")
        await pilot.pause()
        assert not app.is_running and app.return_value == "stop"


@pytest.mark.anyio
async def test_an_error_in_a_screen_is_logged_and_the_tui_goes_on(live):
    async with tui_on(live) as pilot:
        app = pilot.app
        await settle(pilot)
        app.screens["projects"].show({"projects": [{"no": "slug"}], "sig": "x"})  # a broken answer: no slug, no title
        app.screens["projects"].fetch("boom", lambda: 1 / 0, print)
        await settle(pilot)
        assert app.is_running and len(app.problems) == 1 and "ZeroDivisionError" in app.problems[0]


@pytest.mark.anyio
async def test_release_gives_the_terminal_back_when_the_app_ends_another_way(live, monkeypatch):
    """Quit in the browser, `decomp stop` or an update end the process from the server's thread: the TUI is closed
    first (so Textual puts the terminal back), not killed under it."""
    async with tui_on(live) as pilot:
        monkeypatch.setattr(tui, "_running", pilot.app)
        tui._ended.clear()  # (as tui.run does when it starts one)
        done = threading.Thread(target=tui.release, kwargs={"timeout": 0.5})
        done.start()
        for _ in range(50):
            await pilot.pause(0.02)
            if not pilot.app.is_running:
                break
        done.join(2)
        assert not pilot.app.is_running
    tui._leaving.clear()


def test_serve_keeps_running_when_the_tui_ends_by_an_error(monkeypatch, tmp_path, capsys):
    """Only the TUI's own "stop everything?" stops the app: a TUI that ends any other way leaves it serving the
    browser, with the link printed."""
    stopped = []

    class Server:
        def __init__(self, config):
            pass

        def run(self, sockets):
            pass

    from decompnet.app.sessions import Sessions
    monkeypatch.setattr("signal.signal", lambda *how: None)  # (not pytest's own Ctrl-C)
    monkeypatch.setattr(uvicorn, "Server", Server)
    monkeypatch.setattr(cli, "server_config", lambda app: None)
    for ended, quits_before_waiting in (("an error in it", False), ("stop", True)):
        monkeypatch.setattr(tui, "run", lambda port, secret, new_code, e=ended, **about: e)
        waited = []

        def wait(waited=waited):  # (the real one never returns: a signal ends the process, stop_on)
            waited.append(True)

        cli._serve(None, None, 6464, SECRET, tmp_path, Sessions(), None, lambda: stopped.append(True), wait=wait)
        assert bool(waited) is not quits_before_waiting and stopped.pop()
    assert "the TUI ended (an error in it" in capsys.readouterr().out


def test_the_map_colours_come_from_the_windows_stylesheets():
    """The window's own CSS, read when the TUI starts (colors.py): the plain fills are exactly the window's."""
    for dark in (False, True):
        palette = colors.window_palette(dark)
        assert set(palette) == set(mapgrid.ORDER) and all(c.startswith("#") for c in palette.values())
        assert palette["matched"] == "#1b9247" and palette["not C"] == "#d73337"  # oklch(0.58 0.15 150), (0.58 0.20 25)
    assert colors.window_palette(True)["open"] == "#edebe5" and colors.window_palette(False)["open"] == "#18160f"
    assert len(set(colors.window_palette(True).values())) == 6  # six statuses, six colours


def test_a_terminal_without_256_colours_gets_the_basic_ones():
    assert full_colour({"TERM": "xterm-256color"}) and full_colour({"TERM": "screen", "COLORTERM": "truecolor"})
    assert not full_colour({"TERM": "linux"}) and not full_colour({"TERM": "screen"})
    assert len(set(colors.CONSOLE.values())) == 6


@pytest.mark.anyio
async def test_serve_starts_on_the_browser_link_and_says_where_projects_are(tmp_path):
    """`decomp serve` opens on the one-time link (pairing the browser is the first thing to do); with no projects,
    screen 1 says where it looked, since a development copy has its own home (edition.py)."""
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    net = RomNetwork(home / ".decomp" / "decomp.db", home)
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    mcp, _, security, _ = build(net, home / ".decomp", port, SECRET)
    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                                           host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        app = DecompApp(Client(port, SECRET), lambda: "a-fresh-code", name="decompdecomp dev", home="~/Decomp-dev",
                        show_link=True)
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot)
            assert type(app.screen).__name__ == "BrowserLink"
            assert "/pair?code=a-fresh-code" in str(app.screen.query_one("#link").content)
            await pilot.press("escape")
            await settle(pilot)
            said = str(app.screen.query_one("#no-projects").content)
            assert "No projects in ~/Decomp-dev" in said
            assert "decompdecomp dev" in str(app.screen.query_one("TopBar").content)
    finally:
        server.should_exit = True


@pytest.mark.skipif(__import__("os").name != "nt", reason="Ctrl-Break to a process group: Windows")
def test_ctrl_break_stops_serve_gracefully_on_windows(tmp_path):
    """Windows has no `kill` a console program can catch: Ctrl-Break (SIGBREAK) in its console stops `decomp serve`
    as Quit does (exit 0, logged), as `kill` does elsewhere. Here with no terminal (the printed-link path), where the
    main thread waits: a wait Python can't run the handler beside would never end."""
    import os
    import signal
    import subprocess
    import sys

    home = tmp_path / "home"
    env = dict(os.environ, DECOMP_HOME=str(home), DECOMP_UPDATES="off", DECOMP_DETECT_AGENTS="off", DECOMP_TRAY="0")
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        env["DECOMP_PORT"] = str(free.getsockname()[1])
    proc = subprocess.Popen([sys.executable, "-m", "decompnet.app.cli", "serve"], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)  # (Ctrl-Break goes to a group)
    printed: list[str] = []
    threading.Thread(target=lambda: printed.extend(proc.stdout), daemon=True).start()
    try:
        for _ in range(300):
            if any("/pair?code=" in line for line in printed):
                break
            time.sleep(0.1)
        assert any("/pair?code=" in line for line in printed), "".join(printed)
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        assert proc.wait(timeout=30) == 0, "".join(printed)
    finally:
        if proc.poll() is None:
            proc.kill()
    log = (home / ".decomp" / "logs" / "decomp.log").read_text(encoding="utf-8", errors="replace")
    assert "SIGBREAK: stopping" in log


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGTERM") or __import__("os").name == "nt",
                    reason="a signal to a process: POSIX")
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP"])
def test_kill_stops_serve_gracefully(tmp_path, signame):
    """`kill` (SIGTERM), or the terminal going away (SIGHUP), stops `decomp serve` as Quit does — helpers stopped,
    progress saved, exit 0 — rather than killing it where it stands. Here with no terminal (the printed-link path);
    the TUI's path is the same handler."""
    import os
    import signal
    import subprocess
    import sys

    home = tmp_path / "home"
    env = dict(os.environ, DECOMP_HOME=str(home), DECOMP_UPDATES="off", DECOMP_DETECT_AGENTS="off", DECOMP_TRAY="0")
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        env["DECOMP_PORT"] = str(free.getsockname()[1])
    proc = subprocess.Popen([sys.executable, "-m", "decompnet.app.cli", "serve"], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    printed: list[str] = []
    threading.Thread(target=lambda: printed.extend(proc.stdout), daemon=True).start()
    try:
        for _ in range(300):  # it prints the link once it serves, with the handlers in place
            if any("/pair?code=" in line for line in printed):
                break
            time.sleep(0.1)
        assert any("/pair?code=" in line for line in printed), "".join(printed)
        proc.send_signal(getattr(signal, signame))
        assert proc.wait(timeout=30) == 0  # quit_app's exit, not death by the signal (-15, -1)
    finally:
        if proc.poll() is None:
            proc.kill()
    log = (home / ".decomp" / "logs" / "decomp.log").read_text(encoding="utf-8", errors="replace")
    assert f"{signame}: stopping" in log


@pytest.mark.skipif(__import__("os").name == "nt", reason="a terminal (pty) and a signal to a process: POSIX")
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP"])
def test_a_signal_stops_serve_and_its_tui_gives_the_terminal_back(tmp_path, signame):
    """With the TUI in a real terminal (a pty), `kill` or the terminal going away stops `decomp serve` as Quit does
    (exit 0, logged) and the TUI is closed first, so the terminal is put back: its alternate screen left."""
    import fcntl
    import os
    import pty
    import signal
    import struct
    import subprocess
    import sys
    import termios

    import httpx

    home = tmp_path / "home"
    env = dict(os.environ, DECOMP_HOME=str(home), DECOMP_UPDATES="off", DECOMP_DETECT_AGENTS="off", DECOMP_TRAY="0",
               TERM="xterm-256color")
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    env["DECOMP_PORT"] = str(port)
    screen, terminal = pty.openpty()
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    proc = subprocess.Popen([sys.executable, "-m", "decompnet.app.cli", "serve"], env=env, stdin=terminal,
                            stdout=terminal, stderr=terminal, start_new_session=True)
    os.close(terminal)
    shown = bytearray()

    def read() -> None:
        while True:
            try:
                got = os.read(screen, 65536)
            except OSError:  # the process ended: the pty is closed
                return
            if not got:
                return
            shown.extend(got)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        for _ in range(300):  # the TUI is up (it opens on the browser link) and the app answers
            if b"Pair a browser" in shown:
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
            time.sleep(0.1)
        assert b"Pair a browser" in shown, bytes(shown[-2000:])
        before = len(shown)
        proc.send_signal(getattr(signal, signame))
        assert proc.wait(timeout=30) == 0  # quit_app's exit, not death by the signal
        reader.join(5)
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(screen)
    assert b"\x1b[?1049l" in shown[before:]  # Textual left the alternate screen: the terminal is as it was
    log = (home / ".decomp" / "logs" / "decomp.log").read_text(encoding="utf-8", errors="replace")
    assert f"{signame}: stopping" in log


@pytest.mark.anyio
async def test_progress_is_the_share_of_code_as_the_window_shows_it(live):
    """The window's numbers (static/js/base.js, codePct and cFunctions): matched bytes over the code C can make, and
    matched over the functions C can make — set-aside ones out of both. A project of small matches reads low."""
    async with tui_on(live, size=(140, 30)) as pilot:
        await settle(pilot)
        row = [str(cell) for cell in pilot.app.screen.query_one("#projects").get_row_at(1)]
        # matched: 132 + 12 instructions of 4 bytes; code: 1008 instructions, 264 of them set aside
        assert row[2].endswith(" 19.4%") and row[3] == "2/6"
    assert percent(0.0506) == "5.06%" and percent(0.214) == "21.4%"  # as the window writes them


@pytest.mark.anyio
async def test_the_map_before_a_project_is_opened(live):
    """`2` straight away opens the project under screen 1's cursor; on one that can't be read, or with none, it says
    how to pick one, rather than a blank map. Enter on a project that can't be read says why, and stays."""
    async with tui_on(live) as pilot:
        app = pilot.app
        await settle(pilot)
        await pilot.press("enter")  # the first row is the project that can't be read
        await settle(pilot)
        assert type(app.screen).__name__ == "ProjectsScreen" and app.screens["map"].slug is None
        await pilot.press("2")
        await settle(pilot)
        assert "no project open" in str(app.screens["map"].query_one("#cell-line").content)
        await pilot.press("1", "down", "2")  # the readable one under the cursor: its map, without Enter
        await settle(pilot)
        assert app.screens["map"].slug == SLUG and len(app.screens["map"].map_cells) == 8
        assert not app.problems
