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
    original = net._function("clamp_s32", slug)["matched_source"]
    from decompnet.app import polish_checklist

    polish_checklist.save(net, net.project_row(slug)["id"], ["require_name", "compile_64bit", "pointer_casts"], "camelCase")
    brief = net.readability.claim(agent, slug)
    assert "Run check_for_pc with the candidate source" in brief
    assert "uintptr_t/intptr_t" in brief
    assert "clamp_s32" in brief and "must still match byte for byte" in brief
    assert net.readability.submit(agent, slug, "clamp_s32", BROKEN).startswith("❌ It no longer matches")
    tries = net.store.one("SELECT COUNT(*) AS n FROM attempts a JOIN functions f ON f.id=a.function_id "
                          "WHERE f.name='clamp_s32' AND a.score > 0")["n"]
    assert tries == 1  # a polishing helper's compiles count, as a decompiling one's
    assert net.overview(slug)["readable"] == 0
    assert net._function("clamp_s32", slug)["unpolished_source"] is None
    assert net.readability.submit(agent, slug, "clamp_s32", READABLE).startswith("✅ clamp_s32 is readable now")
    fn = net._function("clamp_s32", slug)
    assert fn["status"] == "matched" and "Keeps value between" in fn["matched_source"] and fn["readable"] == 1
    assert net.overview(slug)["readable"] == 1
    for detail in (net.function_preview(slug,"clamp_s32"), net.function_detail(slug,"clamp_s32",include_hints=False)):
        assert detail["polished"] and detail["unpolished_source"] == original
        assert detail["source"] == READABLE
    # its own kinds, so the feed says polishing (not a computer's colour) and routing counts decompiling claims only
    kinds = [r["kind"] for r in net.store.all("SELECT kind FROM events WHERE detail='clamp_s32' ORDER BY id")]
    # the rewrite that stopped matching used up one of its tries (POLISH_TRIES), once for that hand-out
    assert kinds[-3:] == ["polish", "polish_failed", "readable"]


def test_the_same_c_again_is_no_rewrite(matched):
    net, slug, agent = matched
    with pytest.raises(NetworkError, match="already"):
        net.readability.submit(agent, slug, "clamp_s32", net._function("clamp_s32", slug)["matched_source"])


def test_polish_takes_starred_then_big_functions_and_stubs_last(matched):
    """Smallest first, a helper spent its first session on empty functions: they come last now. Not never: a floor of
    8 instructions left 15 of a small Xbox game's 33 functions with address names."""
    from decompnet.app import readable

    net, slug, agent = matched
    pid = net._project(slug)["id"]
    rows = net.store.all("SELECT id, name, n_insns FROM functions WHERE project_id=? AND status!='matched' "
                         "ORDER BY n_insns", pid)
    stub = next(r for r in rows if r["n_insns"] < 8)
    big = next(r for r in reversed(rows) if r["n_insns"] <= readable.BIG_INSNS)
    with net.store.tx() as db:
        for r in (stub, big):
            db.execute("UPDATE functions SET status='matched', matched_source='/* as it matched */' WHERE id=?", (r["id"],))
    assert big["name"] in net.readability.claim(agent, slug)
    net.release(agent, big["name"], "", slug)
    net.store.conn.execute("UPDATE functions SET boost=1 WHERE name='clamp_s32' AND project_id=?", (pid,))
    other, _ = net.local_agent("claude-opus-5.5-2")
    assert "clamp_s32" in net.readability.claim(other, slug)
    assert big["name"] in net.readability.claim(agent, slug)  # the big one before the stub
    net.release(agent, big["name"], "", slug)
    net.store.conn.execute("UPDATE functions SET name='updateWorld', readable=1 WHERE id=?", (big["id"],))
    assert stub["name"] in net.readability.claim(agent, slug)  # then the stub


@pytest.mark.parametrize("require_name", [True, False])
def test_project_mode_games_are_polished_too_through_their_own_files(tmp_path, monkeypatch, require_name):
    """A DS game at 57% had no Polish to choose: polishing was ROM projects' only. In project mode the matching C is
    the project's own file, and a rewrite is judged as any compile of a matched function is there: committed when it
    still matches, the file put back as it was when not."""
    import json

    from decompnet.app import polish_checklist, tools
    from decompnet.app.engine import RomNetwork
    from decompnet.app.forkengine import ForkEngine

    net = RomNetwork(tmp_path / "app.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "platform": "nds", "ready": True}, "x")
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,matched_source) "
                   "VALUES(1,'tripleValue',64,16,'matched','src/a','int tripleValue(int a) { return a * 3; }')")
    monkeypatch.setattr(ForkEngine, "_current_source", lambda self, fp, fn, comment=False: "int tripleValue(int a) { return a * 3; }")
    agent, _ = net.local_agent("claude-sonnet-5-5-1")
    checks = ["compile_64bit", "require_name"] if require_name else ["compile_64bit"]
    polish_checklist.save(net, net.project_row("p")["id"], checks, "snake_case")
    brief = net.readability.claim(agent, "p")
    assert ("snake_case name such as update_actor_target" in brief) is require_name
    assert "Use the project's 64-bit PC build" in brief
    assert "check_for_pc" not in brief and "uintptr_t" not in brief
    assert "MAKE READABLE: tripleValue in src/a" in brief and "edit_file" in brief and "rename_symbol" in brief
    tried = []

    def attempt(self, agent, slug, function, source, keep=True, prelude="", quiet=None, comment=False):
        tried.append(source)
        ok = "/* triple it */" in source
        return {"text": "✅ still matches" if ok else "❌ the matching one was kept", "matched": ok, "score": 0}
    monkeypatch.setattr(ForkEngine, "attempt", attempt)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET name='func_02001000'")
    name = "func_02001000"
    if require_name:
        with pytest.raises(NetworkError, match="rename_symbol"):
            net.readability.submit(agent, "p", name, f"int {name}(int n) {{ return n * 3; }}")
        assert not tried
        with net.store.tx() as db:
            db.execute("UPDATE functions SET name='tripleValue'")
        name = "tripleValue"
    assert "❌" in net.readability.submit(agent, "p", name, f"int {name}(int x) {{ return x + x + x; }}")
    assert net.store.one("SELECT readable FROM functions")["readable"] == 0
    said = net.readability.submit(agent, "p", name, f"/* triple it */\nint {name}(int n) {{ return n * 3; }}")
    assert "readable now" in said and net.store.one("SELECT readable FROM functions")["readable"] == 1
    assert len(tried) == 2
    preview = net.function_preview('p', name)
    assert preview['polished']
    assert preview['unpolished_source'] == "int tripleValue(int a) { return a * 3; }"
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
    s = agents.Session(id="1", kind="api", label="x", slug="g", agent_name="api-m-1",
                       options={"task": "readable"}, state="working")
    assert s.snapshot()["task"] == "readable"


def test_polishing_stats_count_only_accepted_functions_per_agent_and_project(tmp_path):
    import json

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    polisher, _ = net.local_agent("polisher")
    matcher, _ = net.local_agent("matcher")
    with net.store.tx() as db:
        for slug in ("first", "second"):
            db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) "
                       "VALUES(?,?,'','mips',1,?,?)", (slug, slug, str(tmp_path), json.dumps({"kind": "rom"})))
        for agent, kind, detail, project in ((polisher, "polish", "pending", 1),
                                            (polisher, "readable", "accepted", 1),
                                            (polisher, "readable", "accepted", 1),
                                            (polisher, "readable", "other", 2),
                                            (matcher, "claim", "pending", 1)):
            net._event(db, project, agent.id, kind, detail)
    board = net.stats("first")["agents"]
    assert board == [{"agent": "polisher", "compiles": 0, "matched": 0, "polished": 1, "last": None, "improved": 0}]
    assert net.stats("second")["agents"][0]["polished"] == 1


def test_small_polish_plan_passes_the_real_rom_judge_without_moving_lines(matched):
    from decompnet.app.polish_automation import structured
    net,slug,agent = matched
    original = net._function('clamp_s32',slug)['matched_source']
    answer = structured(net.readability,agent,slug,'clamp_s32',
                        '{"v":"value","lo":"low","hi":"high"}', 'Clamp a value to the inclusive range.')
    assert answer.startswith('✅')
    fn = net._function('clamp_s32',slug)
    assert fn['readable'] and fn['matched_source'].count('\n') == original.count('\n')
    assert fn['unpolished_source'] == original
    assert net.project_store(slug).one('SELECT 1 FROM polish_patterns WHERE function_id=?',fn['id'])


@pytest.mark.parametrize('exact', [True, False])
def test_push_poll_timestamp_during_real_polish_still_requires_exact_bytes(matched, monkeypatch, exact):
    import json

    net, slug, agent = matched
    store = net.project_store(slug)
    with store.tx() as db:
        row = db.execute('SELECT id,config FROM projects WHERE slug=?', (slug,)).fetchone()
        cfg = json.loads(row['config'])
        cfg['auto_push'] = {'enabled': True, 'next_check': 1}
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), row['id']))
    original = net._function('clamp_s32', slug)['matched_source']
    compile = net._compile
    def advance_poll(*args):
        result = compile(*args)
        with store.tx() as db:
            db.execute("UPDATE projects SET config=json_set(config,'$.auto_push.next_check',2) WHERE slug=?", (slug,))
        return result
    monkeypatch.setattr(net, '_compile', advance_poll)
    answer = net.readability.submit(agent, slug, 'clamp_s32', READABLE if exact else BROKEN)
    fn = net._function('clamp_s32', slug)
    assert bool(fn['readable']) is exact
    if exact:
        assert answer.startswith('✅') and fn['matched_source'] == READABLE
    else:
        assert answer.startswith('❌ It no longer matches') and fn['matched_source'] == original


def test_generated_names_are_removed_from_existing_and_restored_polished_progress(tmp_path):
    from decompnet.store import Store

    path = tmp_path / "progress.db"
    store = Store(path)
    with store.tx() as db:
        db.execute("INSERT INTO projects(id, slug, name, arch, root, config) VALUES(1, 'p', 'P', 'mips', '', '{}')")
        # Simulate a database saved by an older version.
        db.execute("DROP TRIGGER polished_name_insert")
        db.execute("DROP TRIGGER polished_name_update")
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,readable,matched_source,unpolished_source) "
                   "VALUES(1,'func_80029068',32,8,'matched',1,'edited C','original C'),"
                   "(1,'known_name',32,8,'matched',1,'edited C','original C')")
    store.conn.close()

    upgraded = Store(path)
    rows = upgraded.all("SELECT name,status,readable,matched_source,unpolished_source FROM functions ORDER BY name")
    assert [tuple(row) for row in rows] == [
        ('func_80029068', 'matched', 0, 'edited C', 'original C'),
        ('known_name', 'matched', 1, 'edited C', 'original C'),
    ]
    with upgraded.tx() as db:
        db.execute("UPDATE functions SET readable=1 WHERE name='func_80029068'")
        db.execute("UPDATE functions SET name='_func_00401234@4' WHERE name='known_name'")
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,readable) "
                   "VALUES(1,'fn_80001234',32,8,'matched',1)")
    assert [row['readable'] for row in upgraded.all("SELECT readable FROM functions")] == [0, 0, 0]
    with upgraded.tx() as db:
        # Some object reports keep the original linker symbol after a source rename.
        db.execute("UPDATE functions SET meta=?, readable=1 WHERE name='_func_00401234@4'",
                   ('{"real":"updateActorTarget"}',))
    assert upgraded.one("SELECT readable FROM functions WHERE name='_func_00401234@4'")['readable'] == 1


def test_database_polishing_name_guard_respects_saved_opt_out(tmp_path):
    from decompnet.store import Store

    path = tmp_path / "games.db"
    store = Store(path)
    with store.tx() as db:
        for pid in (1, 2):
            db.execute("INSERT INTO projects(id,slug,name,arch,root,config) VALUES(?,?,?,'mips','','{}')",
                       (pid, f"game-{pid}", f"Game {pid}"))
        db.execute("DROP TRIGGER polished_name_update")
        db.execute("""CREATE TRIGGER polished_name_update AFTER UPDATE OF readable ON functions
                      WHEN NEW.readable=1 BEGIN UPDATE functions SET readable=0 WHERE id=NEW.id; END""")
    store.conn.close()

    upgraded = Store(path)
    with upgraded.tx() as db:
        db.execute("INSERT INTO settings VALUES (?, ?)",
                   ("polishing-checklist:2", '{"selected":[],"name_format":"PascalCase"}'))
        for pid in (1, 2):
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status) VALUES(?,'func_80001234',32,8,'matched')",
                       (pid,))
        db.execute("UPDATE functions SET readable=1")
    upgraded.conn.close()

    reopened = Store(path)
    assert [row["readable"] for row in reopened.all("SELECT readable FROM functions ORDER BY project_id")] == [0, 1]


def test_polish_prompt_defers_naming_style_to_the_game_checklist():
    from decompnet.app.prompt_settings import defaults

    prompt = defaults()['initial_readable']
    assert "Follow the game's optional polishing checklist" in prompt
    assert 'camelCase' not in prompt
