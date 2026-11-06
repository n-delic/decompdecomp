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
    said = net.readability.claim(other, slug)
    assert "lib_a" not in said
    # game_a, held, is all that's left: told to stop, the other helper left for good, and nobody took game_a if its
    # holder's claim ran out (#89)
    assert "Nothing to take right now" in said and "game_a (" in said and "Stop here" not in said


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
    net.store.conn.execute("UPDATE functions SET n_insns=4 WHERE name='game_b'")  # a stub too: it comes last, not never
    assert m._left(slug, "readable") == 1
    net.store.conn.execute("UPDATE functions SET readable=1 WHERE name='game_b'")
    assert m._left(slug, "readable") == 1  # the file to tidy now
    with net.store.tx() as db:
        net._event(db, net._project(slug)["id"], None, "tidied", "game")
    assert m._left(slug, "readable") == 0


def test_polishing_takes_small_functions_last_and_leaves_one_it_keeps_failing_on(tmp_path, monkeypatch):
    """A floor of 8 instructions left 15 of a small Xbox game's 33 functions with address names: small ones come last
    now, not never. And biggest first, one no rewrite kept matching was handed out again and again, to every helper:
    after POLISH_TRIES tries since it was last readable it's left, and helpers stop once only such functions remain."""
    from decompnet.app import readable

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)
    net.forks.sync(slug, log=lambda *_: None)
    net.store.conn.execute("UPDATE functions SET n_insns=2 WHERE name='game_a'")  # a getter: 8 bytes
    net.store.conn.execute("UPDATE functions SET n_insns=50, status='matched' WHERE name='game_b'")
    monkeypatch.setattr(type(net.forks), "_current_source", lambda self, fp, fn, comment=False: None)
    proj = net._project(slug)
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    assert readable.Readability(net).left(proj) == 2
    for _ in range(readable.POLISH_TRIES):  # game_b, the bigger, first: given up on each time
        assert "MAKE READABLE: game_b" in net.readability.claim(agent, slug)
        net.release(agent, "game_b", "", slug)
    assert net.readability.left(proj) == 1
    assert "MAKE READABLE: game_a" in net.readability.claim(agent, slug)  # then the small one
    net.release(agent, "game_a", "", slug)
    net.store.conn.execute("UPDATE functions SET readable=1 WHERE name='game_a'")
    assert net.readability.left(proj) == 0 and "Stop here" in net.readability.claim(agent, slug)
    # made readable since (by hand), then its flag reset: it has its tries again
    with net.store.tx() as db:
        net._event(db, proj["id"], agent.id, "readable", "game_b")
    assert net.readability.left(proj) == 1 and "game_b" in net.readability.claim(agent, slug)


def test_a_held_function_taken_up_again_counts_as_a_try(tmp_path, monkeypatch):
    """A helper whose every rewrite of one function failed to compile kept its claim (each compile renewed it) and was
    handed the same function at the start of each session, 174 times in an afternoon on an Xbox game: only a new claim
    counted as a try. Taking a held one up again counts too, and after POLISH_TRIES it's let go for the next."""
    from decompnet.app import readable

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)
    net.forks.sync(slug, log=lambda *_: None)
    net.store.conn.execute("UPDATE functions SET n_insns=50, status='matched' WHERE name='game_b'")
    net.store.conn.execute("UPDATE functions SET n_insns=20 WHERE name='game_a'")
    monkeypatch.setattr(type(net.forks), "_current_source", lambda self, fp, fn, comment=False: None)
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    for _ in range(readable.POLISH_TRIES):  # never released: each session's claim_readable gives it back
        said = net.readability.claim(agent, slug)
        assert said.startswith("MAKE READABLE: game_b")
    said = net.readability.claim(agent, slug)
    assert said.startswith(f"You held game_b; it was tried {readable.POLISH_TRIES} times without a rewrite that kept it "
                           "matching")
    assert "MAKE READABLE: game_a" in said
    held = net.store.all("SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id")
    assert [r["name"] for r in held] == ["game_a"]
    # made readable (or set aside) while held, after one try: let go too, without being called tried out
    net.store.conn.execute("UPDATE functions SET readable=1 WHERE name='game_a'")
    said = net.readability.claim(agent, slug)
    assert "You held" not in said and "Stop here" in said
    assert net.store.all("SELECT * FROM claims") == []


def test_a_held_function_set_aside_while_held_is_let_go_without_a_try_line(tmp_path, monkeypatch):
    """Set aside while a helper holds it, a matched function leaves TODO, so the claim is let go for the next one: its
    tries aren't up, so the helper isn't told it was tried out (that line is for one that had its POLISH_TRIES)."""
    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)
    net.forks.sync(slug, log=lambda *_: None)
    net.store.conn.execute("UPDATE functions SET n_insns=50, status='matched' WHERE name='game_b'")
    net.store.conn.execute("UPDATE functions SET n_insns=20 WHERE name='game_a'")
    monkeypatch.setattr(type(net.forks), "_current_source", lambda self, fp, fn, comment=False: None)
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    assert "MAKE READABLE: game_b" in net.readability.claim(agent, slug)  # one try
    # set aside as library code while held (objdiff finds it in the project's set_aside.json: matched, meta set_aside)
    net.store.conn.execute("UPDATE functions SET meta=? WHERE name='game_b'", (json.dumps({"set_aside": True}),))
    said = net.readability.claim(agent, slug)
    assert "You held" not in said and "MAKE READABLE: game_a" in said
    held = net.store.all("SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id")
    assert [r["name"] for r in held] == ["game_a"]
    assert net.store.one("SELECT COUNT(*) AS n FROM claims c JOIN functions f ON f.id=c.function_id "
                         "WHERE f.name='game_b'")["n"] == 0


def test_polishing_takes_big_functions_after_the_others_with_a_brief_to_match(tmp_path, monkeypatch):
    """A ceiling of 400 instructions left a game's biggest functions unpolished for good ("they wait"): they come after
    the functions people read most, smallest of them first, and their brief asks for names and comments in place."""
    from decompnet.app import readable

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)
    net.forks.sync(slug, log=lambda *_: None)
    net.store.conn.execute(f"UPDATE functions SET n_insns={readable.BIG_INSNS + 100} WHERE name='game_a'")
    net.store.conn.execute("UPDATE functions SET n_insns=50, status='matched' WHERE name='game_b'")
    monkeypatch.setattr(type(net.forks), "_current_source", lambda self, fp, fn, comment=False: None)
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    first = net.readability.claim(agent, slug)
    assert "MAKE READABLE: game_b" in first and "big function" not in first
    net.store.conn.execute("UPDATE functions SET readable=1 WHERE name='game_b'")
    net.release(agent, "game_b", "", slug)
    second = net.readability.claim(agent, slug)
    assert "MAKE READABLE: game_a" in second and "it's a big function" in second


def test_a_file_whose_functions_are_all_readable_is_tidied_next(tmp_path, monkeypatch):
    """Polishing one function at a time left an Xbox game's types as the first guesses were, declared again in three
    files, among duplicate and unused declarations, and two getters misnamed for want of reading the strings they
    returned. Once a file's functions are readable, it's handed out to tidy, one helper at a time, until tidy_done."""
    from decompnet.app import readable, tools

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)
    net.forks.sync(slug, log=lambda *_: None)
    net.store.conn.execute("UPDATE functions SET n_insns=16, status='matched' WHERE name='game_b'")
    monkeypatch.setattr(type(net.forks), "_current_source", lambda self, fp, fn, comment=False: None)
    proj = net._project(slug)
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    other, _ = net.local_agent("claude-sonnet-5-5-2")
    brief = net.readability.claim(agent, slug)
    assert "read_data a global before you name it" in brief and "style the names around them" in brief
    net.release(agent, brief.split()[2], "", slug)
    brief = net.readability.claim(other, slug)
    assert "TIDY" not in brief  # a function of the file isn't readable yet
    net.release(other, brief.split()[2], "", slug)
    net.store.conn.execute("UPDATE functions SET readable=1 WHERE name IN ('game_a', 'game_b')")
    assert net.readability.left(proj) == 1  # the file
    # what the globals it declares hold comes with it: two getters of class-name strings were named "static data"
    (root / "src").mkdir()
    (root / "src" / "game.c").write_text('extern int g_shape_static_data;\nextern float k_ratio;\nextern "C" void f(void);\n'
                                         "extern int D_00403000[4];\nint get(void);\n")
    held = {"g_shape_static_data": 'D_00402000 (.rdata+0x0, 32 bytes)\n  +0x000: 43 69 72\n'
                                   'as f32: 4.46528e+30, 1.49939e-43\nas a string: "Circle"',  # (text reads as floats too)
            "k_ratio": "D_00402010 (.rdata+0x10, 32 bytes)\n  +0x000: 00 00 80 3f\nas f32: 1",
            "D_00403000": "D_00403000 (.bss+0x0, 32 bytes): uninitialized data (.bss), zeros at start-up."}

    def read_data(agent, slug, what, length=64):
        if what not in held:
            raise NetworkError(f"no data {what}")
        return held[what]
    monkeypatch.setattr(net.forks, "read_data", read_data)
    job = net.readability.claim(agent, slug)
    assert job.startswith("TIDY A FILE: game (src/game.c)") and 'tidy_done("game"' in job
    assert '   g_shape_static_data: as a string: "Circle"' in job and "   k_ratio: as f32: 1" in job
    assert "   D_00403000: uninitialized data (.bss)" in job and "   f:" not in job
    assert net.readability.claim(agent, slug) == job  # it holds it
    said = net.readability.claim(other, slug)  # one file, one helper; but come back, don't stop (#89)
    assert "Nothing to take right now" in said and "the file game (" in said and "Stop here" not in said
    with pytest.raises(NetworkError, match="aren't tidying"):
        net.readability.tidy_done(other, slug, "game", "everything")
    with pytest.raises(NetworkError, match="summary"):
        net.readability.tidy_done(agent, slug, "game", " ")
    # a claim that ran out is no claim: the file is the next helper's to tidy, not the late one's to mark tidy (#89)
    net.readability._tidying[(proj["id"], "game")] = (agent.id, time.time() - 1)
    with pytest.raises(NetworkError, match="aren't tidying"):
        net.readability.tidy_done(agent, slug, "game", "late")
    assert net.readability.claim(other, slug) == job
    with pytest.raises(NetworkError, match="aren't tidying"):
        net.readability.tidy_done(agent, slug, "game", "late")
    assert net.readability.tidy_done(other, slug, "game", "Particle in include/particle.h").startswith("✅")
    assert net.readability.left(proj) == 0 and "Stop here" in net.readability.claim(agent, slug)
    kinds = [r["kind"] for r in net.store.all("SELECT kind FROM events WHERE kind LIKE 'tid%' ORDER BY id")]
    # the second claim of a held file is a try too (#108): one tidy for each of the three hand-outs
    assert kinds == ["tidy", "tidy", "tidy", "tidied", "tidy_summary"]
    # a file no helper finishes is left after its tries, as a function is
    net.store.conn.execute("DELETE FROM events WHERE kind LIKE 'tid%'")
    for _ in range(readable.POLISH_TRIES):
        assert net.readability.claim(agent, slug).startswith("TIDY A FILE")
        net.readability._tidying.clear()  # (its claim ran out)
    assert net.readability.left(proj) == 0
    assert "tidy_done" in tools.tool_names(fork=True) and "tidy_done" not in tools.tool_names(fork=False)


def test_a_held_file_taken_up_again_counts_as_a_try(tmp_path, monkeypatch):
    """A helper that never finished one file got it back at every claim with no try against it, so a file it kept
    failing to tidy never left and left() never reached 0: taking a held file up again counts as a try too, and after
    POLISH_TRIES it's let go, as a function is."""
    from decompnet.app import readable

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)
    net.forks.sync(slug, log=lambda *_: None)
    net.store.conn.execute("UPDATE functions SET n_insns=16, status='matched' WHERE name='game_b'")
    net.store.conn.execute("UPDATE functions SET readable=1 WHERE name IN ('game_a', 'game_b')")
    proj = net._project(slug)
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    assert net.readability.left(proj) == 1  # the file
    for _ in range(readable.POLISH_TRIES):  # never cleared: each claim_readable gives the same helper its file back
        assert net.readability.claim(agent, slug).startswith("TIDY A FILE: game")
    said = net.readability.claim(agent, slug)
    assert said.startswith(f"You held game; it was tried {readable.POLISH_TRIES} times without being tidied")
    assert "TIDY A FILE" not in said and "Stop here" in said
    assert net.readability.left(proj) == 0 and net.readability._tidying == {}
    kinds = [r["kind"] for r in net.store.all("SELECT kind FROM events WHERE kind='tidy'")]
    assert len(kinds) == readable.POLISH_TRIES


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


def test_re_measuring_builds_what_changed_first(tmp_path, monkeypatch):
    """A measure reads build/ as it is: an Xbox game rolled back with git outside decompdecomp measured its broken build
    again and again (27 of 33) until it was built by hand. Re-measure builds first, and a unit whose source doesn't
    build doesn't count what its last object matched. Making the project doesn't build here (its import does that)."""
    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    slug = _project(net, root, monkeypatch)
    units = [Unit("game", None, root / "build" / "game.o", "src/game.c", False, None), *UNITS[1:]]
    monkeypatch.setattr(ForkProject, "units", lambda self: units)
    builds = []

    def build_all(self, log=lambda *_: None, failed=None):
        builds.append(1)
        if failed is not None and len(builds) > 1:  # the second time, game.c doesn't compile
            failed.append("build/game.o")
        return True, ""
    monkeypatch.setattr(ForkProject, "build_all", build_all)
    net.forks.check(slug, log=lambda *_: None)
    assert builds == [] and _sums(net) == (2, 1)
    net.forks.check(slug, log=lambda *_: None, build=True)
    assert builds == [1] and _sums(net) == (2, 1)
    net.forks.check(slug, log=lambda *_: None, build=True)
    assert builds == [1, 1] and _statuses(net)["game_a"] != "matched"
