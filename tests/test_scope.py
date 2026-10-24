"""What a project covers (forks/project.py SCOPES): the game's own code, with the library code its import set aside
kept from agents and out of progress, or the whole program. Chosen at import, kept in the project's manifest,
changeable in its Settings."""

import json
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from decompnet import consoles
from decompnet.app import window
from decompnet.app.engine import RomNetwork
from decompnet.app.server import build
from decompnet.forks.project import ForkProject, Unit
from decompnet.network import NetworkError, set_aside

PORT, SECRET = 6498, "s3cret-for-scope-tests"
WINDOW = {"User-Agent": f"Mozilla/5.0 AppleWebKit/605.1.15 {window.TOKEN_AGENT}{SECRET}", "X-Decomp": "1"}


def _fn(name: str, addr: int, pct: float | None = None) -> dict:
    return {"name": name, "size": "16", "metadata": {"virtual_address": str(addr)},
            **({"fuzzy_match_percent": pct} if pct is not None else {})}


# the game's own two functions, two of library code with source files, one of library code without one
REPORT = {"measures": {"total_functions": 5, "matched_functions": 2, "total_code": "80", "matched_code": "32"},
          "units": [{"name": "game", "functions": [_fn("game_a", 0x1000, 100.0), _fn("game_b", 0x1010, 40.0)]},
                    {"name": "lib", "functions": [_fn("lib_a", 0x2000, 100.0), _fn("lib_b", 0x2010)]},
                    {"name": "lib_nosrc", "functions": [_fn("lib_c", 0x3000)]}]}
UNITS = [Unit("game", None, None, "src/game.c", False, None), Unit("lib", None, None, "src/lib.c", False, None),
         Unit("lib_nosrc", None, None, None, False, None)]


def _project(net: RomNetwork, root: Path, monkeypatch, manifest: dict | None = None) -> str:
    """A project from scratch whose import set three functions aside (library code)."""
    (root / "config" / "v").mkdir(parents=True)
    (root / "config" / "v" / "set_aside.json").write_text(json.dumps(
        {"lib_a": "library code (an SDK)", "lib_b": "library code (an SDK)", "lib_c": "the C runtime"}))
    (root / ".decomp.wtf.json").write_text(json.dumps({"created": "from scratch", "platform": "pc", "version": "v",
                                                       "set_aside": "config/v/set_aside.json", **(manifest or {})}))
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "origin": "scratch", "version": "v",
                                                  "fork_kind": "manifest", "platform": "pc", "ready": False}, "x")
    monkeypatch.setattr(ForkProject, "report", lambda self: REPORT)
    monkeypatch.setattr(ForkProject, "units", lambda self: UNITS)
    return "p"


def _statuses(net: RomNetwork) -> dict[str, str]:
    return {r["name"]: r["status"] for r in net.store.all("SELECT name, status FROM functions")}


def _sums(net: RomNetwork) -> tuple[int, int]:
    """What the project list and header show: functions less the set-aside ones (base.js cFunctions), and matched
    ones (network.PROGRESS_COLUMNS: a set-aside one, for either reason, doesn't count)."""
    s = net.summary("p")
    return s["functions"] - s["set_aside"], s["matched"]


def test_polishing_takes_the_games_own_code_and_leaves_set_aside_library_code(tmp_path, monkeypatch):
    """An Xbox game's matched SDK functions, set aside as library code, would have been polished with its own: polishing
    takes and counts the game's own code, as progress does."""
    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)
    net.forks.sync(slug, log=lambda *_: None)
    net.store.conn.execute("UPDATE functions SET n_insns=16, matched_source='int f(void) { return 0; }'")
    monkeypatch.setattr(type(net.forks), "_current_source", lambda self, fp, fn, comment=False: None)
    assert net.readability.counts(net._project(slug)) == {"matched": 1, "readable": 0}  # game_a, not lib_a
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    assert "MAKE READABLE: game_a" in net.readability.claim(agent, slug)
    other, _ = net.local_agent("claude-sonnet-5-5-2")
    assert "lib_a" not in net.readability.claim(other, slug)


def test_polishing_helpers_carry_on_once_nothing_is_left_to_decompile(tmp_path, monkeypatch):
    """Eight polishing helpers on an Xbox game with every function of its own matched all stopped at once: "nothing
    left to decompile here". A helper's session asks what its task has left: polishing counts what it can still take."""
    from decompnet.app import agents

    from .test_agents import URL

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)
    net.forks.sync(slug, log=lambda *_: None)
    net.store.conn.execute("UPDATE functions SET n_insns=16")
    net.store.conn.execute("UPDATE functions SET status='matched' WHERE name='game_b'")
    m = agents.Manager(net, tmp_path, URL)
    assert m._left(slug) == 0  # nothing left to decompile
    assert m._left(slug, "readable") == 2  # game_a and game_b to polish (not lib_a: library code, set aside)
    net.store.conn.execute("UPDATE functions SET readable=1 WHERE name='game_a'")
    net.store.conn.execute("UPDATE functions SET n_insns=4 WHERE name='game_b'")  # a stub: nothing to explain
    assert m._left(slug, "readable") == 0


def test_a_manifest_without_a_scope_covers_the_games_own_code_and_the_scope_round_trips(tmp_path):
    fp = ForkProject(tmp_path)
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps({"version": "v", "rebuild": {"identical": True}}))
    assert fp.scope == "game"
    fp.update_manifest(scope="program")
    assert fp.scope == "program" and fp.manifest == {"version": "v", "rebuild": {"identical": True}, "scope": "program"}
    fp.update_manifest(scope="game")
    assert ForkProject(tmp_path).scope == "game"
    fp.update_manifest(scope=None)
    assert "scope" not in fp.manifest and fp.scope == "game"
    fp.update_manifest(scope="anything else")
    assert fp.scope == "game"


def test_set_aside_code_is_kept_from_agents_for_the_game_and_open_for_the_whole_program(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)
    m = net.forks.sync(slug, log=lambda *_: None)
    assert _statuses(net) == {"game_a": "matched", "game_b": "open", "lib_a": "matched", "lib_b": "asm",
                              "lib_c": "nosource"}
    assert _sums(net) == (2, 1)  # the game's own two, one matched (lib_a's match doesn't count)
    assert (m["total_functions"], m["matched_functions"], m["set_aside_functions"]) == (2, 1, 3)
    ov = net.forks.overview(slug)
    # set aside is one count for both reasons, not the game's own code here: the pages take it off the total
    assert (ov["scope"], ov["left_out"], ov["functions"], ov["set_aside"], ov["matched"]) == ("game", 3, 5, 3, 1)
    assert ov["scope_list"] == {"functions": 3, "without_source": 1} and ov["unanalysed"] is None
    assert ov["set_aside_bytes"] == 48 and ov["matched_bytes"] == 16

    # hand-written assembly (here: what a twin says) is the game's own and isn't the scope's to change
    row = net.store.one("SELECT id, meta FROM functions WHERE name='game_b'")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET meta=? WHERE id=?",
                   (json.dumps({**json.loads(row["meta"]), "asm_code": "hand-written assembly"}), row["id"]))

    m = net.forks.set_scope(slug, "program", log=lambda *_: None)
    assert json.loads((root / ".decomp.wtf.json").read_text())["scope"] == "program"
    assert _statuses(net) == {"game_a": "matched", "game_b": "asm", "lib_a": "matched", "lib_b": "open",
                              "lib_c": "nosource"}
    assert not any(json.loads(r["meta"]).get("set_aside") for r in net.store.all("SELECT meta FROM functions"))
    assert _sums(net) == (4, 2)  # every function counts, less game_b: no C makes it
    assert m == REPORT["measures"]  # nothing left out of the measures
    ov = net.forks.overview(slug)
    assert (ov["scope"], ov["left_out"], ov["functions"], ov["set_aside"]) == ("program", 0, 5, 1)
    assert ov["scope_list"] == {"functions": 3, "without_source": 1}  # still what the list names, for Settings

    net.forks.set_scope(slug, "game", log=lambda *_: None)  # and back
    assert _statuses(net) == {"game_a": "matched", "game_b": "asm", "lib_a": "matched", "lib_b": "asm",
                              "lib_c": "nosource"}
    assert _sums(net) == (1, 1)  # game_b: set aside as not C, the library code as not the game's
    # a function's page says why for either reason, a matched library function too
    why = {r["name"]: set_aside(r).get("set_aside") for r in net.store.all("SELECT name, status, meta FROM functions")}
    assert why == {"game_a": None, "game_b": "hand-written assembly", "lib_a": "library code (an SDK)",
                   "lib_b": "library code (an SDK)", "lib_c": "the C runtime"}
    with pytest.raises(NetworkError):
        net.forks.set_scope(slug, "everything", log=lambda *_: None)


def _unanalysed(monkeypatch, left: list[str]) -> list[str]:
    """The project's console says its import left `left[0]` unanalysed (Console.unanalysed) until a new analysis
    brings it in (analyse_unanalysed); returns the slugs analysed."""
    console = type(consoles.get("pc"))
    ran = []

    def analyse(self, net, slug, log=print):
        ran.append(slug)
        left.clear()
        net.forks.sync(slug, log=log)  # (as reanalyse does once it keeps a change)
        return "the SDK's own sections are analysed now"
    monkeypatch.setattr(console, "unanalysed", lambda self, fp: left[0] if left else None)
    monkeypatch.setattr(console, "analyse_unanalysed", analyse)
    return ran


def test_code_an_older_import_left_out_is_offered_and_brought_in_once(tmp_path, monkeypatch):
    """An older import's manifest may still say what it left out (`set_aside_unimported`): nothing reads it. Settings
    offers what the console says a new analysis can bring in, and choosing the whole program brings it in too."""
    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch, {"set_aside_unimported": "the SDK's own sections"})
    net.forks.sync(slug, log=lambda *_: None)
    ov = net.forks.overview(slug)
    assert "unimported" not in ov and ov["unanalysed"] is None

    ran = _unanalysed(monkeypatch, ["the SDK's own sections"])
    assert net.forks.overview(slug)["unanalysed"] == "the SDK's own sections"
    net.forks.set_scope(slug, "game", log=lambda *_: None)  # the game's own code: offered, not run
    assert ran == [] and net.forks.overview(slug)["unanalysed"] == "the SDK's own sections"
    m = net.forks.set_scope(slug, "program", log=lambda *_: None)
    assert ran == [slug] and m == REPORT["measures"] and _statuses(net)["lib_b"] == "open"
    ov = net.forks.overview(slug)
    assert (ov["scope"], ov["unanalysed"]) == ("program", None)
    assert net.forks.analyse_unanalysed(slug, log=lambda *_: None) is None and ran == [slug]  # nothing left: once
    net.forks.set_scope(slug, "game", log=lambda *_: None)
    net.forks.set_scope(slug, "program", log=lambda *_: None)
    assert ran == [slug]


def test_a_console_that_fails_to_say_what_is_unanalysed_offers_nothing(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)

    def broken(self, fp):
        raise OSError("the program isn't there")
    monkeypatch.setattr(type(consoles.get("pc")), "unanalysed", broken)
    net.forks.sync(slug, log=lambda *_: None)
    assert net.forks.overview(slug)["unanalysed"] is None
    assert net.forks.analyse_unanalysed(slug, log=lambda *_: None) is None


class _Recognising(consoles.Console):
    """A console whose import sets library code aside, as a from-scratch importer that finds an SDK's code does."""
    id, name, mode, sets_aside = "fake", "Fake", consoles.PROJECT, True

    def __init__(self):
        super().__init__()
        self.asked: list[str] = []

    def game_info(self, game: Path) -> dict:
        return {"platform": "fake", "id": "g", "title": "A game", "sha1": "ab" * 20}

    def create_project(self, root: Path, game: Path, *, compiler=None, log=print, scope: str = "game") -> dict:
        self.asked.append(scope)
        root.mkdir(parents=True)
        (root / ".decomp.wtf.json").write_text(json.dumps({"created": "from scratch", "platform": "fake", "version": "g"}))
        return {"platform": "fake", "id": "g", "title": "A game", "compiler": "c"}


def test_the_import_choice_reaches_the_importer_and_the_manifest(tmp_path, monkeypatch):
    from decompnet.app.forkengine import ForkEngine

    monkeypatch.setattr(ForkEngine, "check", lambda self, slug, log=print: {})
    game = tmp_path / "game.bin"
    game.write_bytes(b"x")
    con = _Recognising()
    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    slug = con.import_game(net, game, scope="program", log=lambda *_: None)
    assert con.asked == ["program"]
    assert json.loads((tmp_path / "ws" / slug / ".decomp.wtf.json").read_text())["scope"] == "program"

    net2 = RomNetwork(tmp_path / "decomp2.db", tmp_path / "ws2")
    slug = con.import_game(net2, game, log=lambda *_: None)  # no choice made: the game's own code
    assert con.asked[-1] == "game"
    assert json.loads((tmp_path / "ws2" / slug / ".decomp.wtf.json").read_text())["scope"] == "game"

    plain = consoles.Console(id="plain", mode=consoles.PROJECT)  # a console that sets nothing aside: no scope
    seen = {}
    monkeypatch.setattr(net2.forks, "create_scratch", lambda path, console, **kw: seen.update(kw) or "s")
    plain.import_game(net2, game, scope="program", log=lambda *_: None)
    assert seen["scope"] is None
    assert con.describe()["sets_aside"] is True and plain.describe()["sets_aside"] is False


@pytest.fixture
def client(tmp_path):
    home = tmp_path / "home"
    (home / ".decomp").mkdir(parents=True)
    net = RomNetwork(home / ".decomp" / "decomp.db", home)
    mcp, _, security, _ = build(net, home / ".decomp", PORT, SECRET)
    c = TestClient(mcp.streamable_http_app(transport_security=security, host="127.0.0.1"),
                   base_url=f"http://127.0.0.1:{PORT}", headers=WINDOW)
    assert c.post("/api/settings", json={"autopilot": False}).status_code == 200
    return c, net, home


def _job(c: TestClient, job: str) -> dict:
    for _ in range(300):
        j = c.get(f"/api/job?id={job}").json()
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("the job didn't finish")


def test_the_import_dialogs_choice_is_carried_by_the_import_route(client, tmp_path, monkeypatch):
    c, net, _ = client
    seen = {}
    monkeypatch.setattr(type(net), "import_rom", lambda self, game, **kw: seen.update(kw) or "s")
    game = tmp_path / "game.bin"
    game.write_bytes(b"x")
    j = _job(c, c.post(f"/api/import?confirmed=1&scope=program&path={game}", content=b"").json()["job"])
    assert j["state"] == "done" and seen["scope"] == "program"
    _job(c, c.post(f"/api/import?confirmed=1&path={game}", content=b"").json()["job"])
    assert seen["scope"] is None
    assert c.post(f"/api/import?confirmed=1&scope=most&path={game}", content=b"").status_code == 400


def test_the_settings_route_changes_the_scope_and_measures_again(client, monkeypatch):
    c, net, home = client
    root = home / "p"
    root.mkdir()
    slug = _project(net, root, monkeypatch)
    net.forks.sync(slug, log=lambda *_: None)
    j = _job(c, c.post("/api/scope", json={"slug": slug, "scope": "program"}).json()["job"])
    assert j["state"] == "done", j
    assert (j["result"]["scope"], j["result"]["left_out"], j["result"]["functions"]) == ("program", 0, 5)
    assert json.loads((root / ".decomp.wtf.json").read_text())["scope"] == "program"
    assert _statuses(net)["lib_b"] == "open"
    assert c.post("/api/scope", json={"slug": slug, "scope": "most"}).status_code == 400
    j = _job(c, c.post("/api/scope", json={"slug": slug, "scope": "game"}).json()["job"])
    assert j["result"]["left_out"] == 3 and _statuses(net)["lib_b"] == "asm"


def test_the_settings_route_analyses_what_the_import_left_out(client, monkeypatch):
    c, net, home = client
    root = home / "p"
    root.mkdir()
    slug = _project(net, root, monkeypatch)
    net.forks.sync(slug, log=lambda *_: None)
    ran = _unanalysed(monkeypatch, ["the SDK's own sections"])
    assert c.get(f"/api/project?slug={slug}").json()["unanalysed"] == "the SDK's own sections"
    j = _job(c, c.post("/api/unanalysed", json={"slug": slug}).json()["job"])
    assert j["state"] == "done", j
    assert ran == [slug] and j["result"]["unanalysed"] is None and j["result"]["scope"] == "game"
    assert _statuses(net)["lib_b"] == "asm"  # brought in, and set aside: the project covers the game's own code
