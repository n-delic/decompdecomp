"""Matched code made readable (app/readable.py): a rewrite with names, types and a comment is kept when the function
still matches byte for byte, refused otherwise, and the project counts how much of its matched code is readable."""

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError

from .test_app_engine import CLAMP
from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)

READABLE = """/* Keeps value between low and high (both included). */
s32 clamp_s32(s32 value, s32 low, s32 high) {
    if (value < low) {
        return low;
    }
    if (value > high) {
        return high;
    }
    return value;
}"""

BROKEN = READABLE.replace("value > high", "value >= high")


@pytest.fixture
def matched(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    assert net.compile_and_diff(agent, "func_800007D0", CLAMP, slug).startswith("✅ MATCH")
    assert net.rename_symbol(agent, slug, "func_800007D0", "clamp_s32").startswith("✅")
    return net, slug, agent


def test_a_readable_rewrite_that_still_matches_is_kept(matched):
    net, slug, agent = matched
    brief = net.readability.claim(agent, slug)
    assert "clamp_s32" in brief and "must still match byte for byte" in brief
    assert net.readability.submit(agent, slug, "clamp_s32", BROKEN).startswith("❌ It no longer matches")
    tries = net.store.one("SELECT COUNT(*) AS n FROM attempts a JOIN functions f ON f.id=a.function_id "
                          "WHERE f.name='clamp_s32' AND a.score > 0")["n"]
    assert tries == 1  # a polishing helper's compiles count, as a decompiling one's
    assert net.overview(slug)["readable"] == 0
    assert net.readability.submit(agent, slug, "clamp_s32", READABLE).startswith("✅ clamp_s32 is readable now")
    fn = net._function("clamp_s32", slug)
    assert fn["status"] == "matched" and "Keeps value between" in fn["matched_source"] and fn["readable"] == 1
    assert net.overview(slug)["readable"] == 1
    # its own kinds, so the feed says polishing (not a computer's colour) and routing counts decompiling claims only
    kinds = [r["kind"] for r in net.store.all("SELECT kind FROM events WHERE detail='clamp_s32' ORDER BY id")]
    assert kinds[-2:] == ["polish", "readable"]


def test_the_same_c_again_is_no_rewrite(matched):
    net, slug, agent = matched
    with pytest.raises(NetworkError, match="already"):
        net.readability.submit(agent, slug, "clamp_s32", net._function("clamp_s32", slug)["matched_source"])


def test_polish_takes_starred_then_big_functions_and_leaves_stubs(matched):
    """Smallest first, a helper spent its first session on empty functions: there's nothing to explain in them."""
    from decompnet.app import readable

    net, slug, agent = matched
    pid = net._project(slug)["id"]
    rows = net.store.all("SELECT id, name, n_insns FROM functions WHERE project_id=? AND status!='matched' "
                         "ORDER BY n_insns", pid)
    stub = next(r for r in rows if r["n_insns"] < readable.MIN_INSNS)
    big = next(r for r in reversed(rows) if r["n_insns"] <= readable.MAX_INSNS)
    with net.store.tx() as db:
        for r in (stub, big):
            db.execute("UPDATE functions SET status='matched', matched_source='/* as it matched */' WHERE id=?", (r["id"],))
    assert big["name"] in net.readability.claim(agent, slug)
    net.release(agent, big["name"], "", slug)
    net.store.conn.execute("UPDATE functions SET boost=1 WHERE name='clamp_s32' AND project_id=?", (pid,))
    other, _ = net.local_agent("claude-opus-5.5-2")
    assert "clamp_s32" in net.readability.claim(other, slug)
    assert stub["name"] not in net.readability.claim(agent, slug)


def test_project_mode_games_are_polished_too_through_their_own_files(tmp_path, monkeypatch):
    """A DS game at 57% had no Polish to choose: polishing was ROM projects' only. In project mode the matching C is
    the project's own file, and a rewrite is judged as any compile of a matched function is there: committed when it
    still matches, the file put back as it was when not."""
    import json

    from decompnet.app import tools
    from decompnet.app.engine import RomNetwork
    from decompnet.app.forkengine import ForkEngine

    net = RomNetwork(tmp_path / "app.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "platform": "nds", "ready": True}, "x")
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,matched_source) "
                   "VALUES(1,'func_02001000',64,16,'matched','src/a','int func_02001000(int a) { return a * 3; }')")
    monkeypatch.setattr(ForkEngine, "_current_source", lambda self, fp, fn, comment=False: "int func_02001000(int a) { return a * 3; }")
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    brief = net.readability.claim(agent, "p")
    assert "MAKE READABLE: func_02001000 in src/a" in brief and "edit_file" in brief and "rename_symbol" in brief
    tried = []

    def attempt(self, agent, slug, function, source, keep=True, prelude="", quiet=None, comment=False):
        tried.append(source)
        ok = "triple" in source
        return {"text": "✅ still matches" if ok else "❌ the matching one was kept", "matched": ok, "score": 0}
    monkeypatch.setattr(ForkEngine, "attempt", attempt)
    assert "❌" in net.readability.submit(agent, "p", "func_02001000", "int func_02001000(int x) { return x + x + x; }")
    assert net.store.one("SELECT readable FROM functions")["readable"] == 0
    said = net.readability.submit(agent, "p", "func_02001000", "/* triple it */\nint func_02001000(int n) { return n * 3; }")
    assert "readable now" in said and net.store.one("SELECT readable FROM functions")["readable"] == 1
    assert len(tried) == 2
    assert "claim_readable" in tools.tool_names(fork=True) and "make_readable" in tools.tool_names(fork=True)
    assert "edit_file" in tools.READABLE_PROMPT.format(title="T", count=1, names=tools.READABLE_FORK)


def test_the_function_list_tells_polished_functions_and_helpers_say_they_polish(tmp_path):
    """Polished functions are matched ones made readable: the Functions tab shows them apart (a Polished filter and
    status), and a polishing helper's card says what it does, so decompiling and polishing helpers read apart."""
    import json

    from decompnet.app import agents
    from decompnet.app.engine import RomNetwork

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES('g','G','','mips',1,?,?)",
                   (str(tmp_path), json.dumps({"kind": "rom"})))
        for name, readable in (("a", 1), ("b", 0)):
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,readable) VALUES(1,?,8,2,'matched','u',?)",
                       (name, readable))
    assert {r["name"]: r["polished"] for r in net.function_map("g")} == {"a": True, "b": False}
    s = agents.Session.__new__(agents.Session)
    s.__dict__.update(id="1", kind="api", label="x", slug="g", agent_name="api-m-1", model="m", state="working",
                      why="", todo="", until=None, stopped_by=None, last_message="", runs=0, restarts=0, error="",
                      started_at=0, usd=0, tokens=0, feed=[], options={"task": "readable"})
    assert s.snapshot()["task"] == "readable"
