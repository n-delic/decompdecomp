"""Which function an agent gets next in a fork: what nobody gave up on, small first, and among functions of about
the same size the one in the file closest to done. A crowd of agents must not spend its time passing the hardest
leftovers around (Mario Party 6: 16 agents, one match, on 326 big functions released again and again)."""

import json

from decompnet import network
from decompnet.app.engine import RomNetwork


def test_fresh_small_functions_first_and_files_close_to_done_among_equals(tmp_path):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "ppc", 1, str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        rows = [("leftover", "a", 12, 3), ("big_fresh", "b", 400, 0), ("small_in_big_file", "c", 20, 0),
                ("small_in_done_file", "d", 22, 0), ("tiny", "c", 4, 0)]
        rows += [(f"other_{i}", "c", 300, 0) for i in range(5)]  # file c has lots left; file d only one
        for name, unit, n, abandons in rows:
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,abandons) VALUES(?,?,?,?,?,?,?)",
                       (pid, name, n * 4, n, "open", unit, abandons))
    order = []
    for k in range(len(rows)):
        agent, _ = net.local_agent(f"a{k}")
        order.append(net.claim_row(agent, "game", None, "finish")["name"])
    # 4 and 22 instructions are about the same size: the file with one function left goes first
    assert order[:3] == ["small_in_done_file", "tiny", "small_in_big_file"], order
    assert order[-1] == "leftover" and order.index("big_fresh") < order.index("leftover"), order


def test_a_function_just_released_waits_before_the_next_agent_gets_it(tmp_path, monkeypatch):
    """Issue #19: a released function goes to the back of the queue for every agent for a while; it comes when
    nothing else is left, or at once when asked for by name. Near-misses come given up on least first."""
    from decompnet import network

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "ppc", 1, str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        # "small" has twins: one release alone doesn't move it down (twins come first until given up on twice)
        for name, n, score, abandons, boost in [("small", 4, None, 0, 3), ("next", 8, None, 0, 0),
                                                ("stuck", 50, 4, 3, 0), ("near", 50, 30, 1, 0)]:
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,best_score,abandons,boost) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (pid, name, n * 4, n, "open", "a", score, abandons, boost))
    a, _ = net.local_agent("a")
    b, _ = net.local_agent("b")
    assert net.claim_row(a, "game", None, "easiest")["name"] == "small"
    net.release(a, "small", "", "game")
    assert net.claim_row(b, "game", None, "easiest")["name"] == "next"  # not the one a just gave up on
    net.release(b, "next", "", "game")
    c, _ = net.local_agent("c")
    assert net.claim_row(c, "game", None, "closest")["name"] == "near"  # given up on once beats three times
    d, _ = net.local_agent("d")
    assert net.claim_row(d, "game", None, "easiest")["name"] == "stuck"  # what wasn't just released
    e, _ = net.local_agent("e")
    assert net.claim_row(e, "game", None, "easiest")["name"] == "small"  # nothing else: the cooling one after all
    f, _ = net.local_agent("f")
    assert net.claim_row(f, "game", "next")["name"] == "next"  # by name: at once
    monkeypatch.setattr(network, "RELEASE_COOLDOWN", 0)
    for g in (e, f):
        net.release(g, net.store.one("SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id "
                                     "WHERE c.agent_id=?", g.id)["name"], "", "game")
    h, _ = net.local_agent("h")
    assert net.claim_row(h, "game", None, "easiest")["name"] == "small"  # the wait is over: the usual order


def test_a_released_function_goes_to_another_model_family_first(tmp_path):
    """Only 5.5% of released functions ever matched later, and another try by the same model mostly fails the same
    way: a function a DeepSeek helper released waits for helpers of another model (after the usual pause), and for
    DeepSeek helpers until the shared header or the compiler changes."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "mips", 0, str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        for name in ("f0", "f1"):
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(?,?,?,?,?,?)",
                       (pid, name, 16, 4, "open", ""))
    a, _ = net.local_agent("opencode-deepseek-v4.1-flash-1")
    b, _ = net.local_agent("api-deepseek-chat-1")
    c, _ = net.local_agent("claude-opus-1")
    assert {network.model_family(x.name) for x in (a, b)} == {"deepseek"} and network.model_family(c.name) == "claude"
    assert net.claim_row(a, "game")["name"] == "f0"
    net.release(a, "f0", "", "game")
    with net.store.tx() as db:  # long ago: the pause for everybody is over
        db.execute("UPDATE events SET created_at=1 WHERE kind='release'")
    assert net.claim_row(b, "game")["name"] == "f1"  # the same family: something else
    assert net.claim_row(c, "game")["name"] == "f0"  # another family: at once
    net.release(c, "f0", "", "game")
    net.release(b, "f1", "", "game")
    with net.store.tx() as db:
        db.execute("UPDATE events SET created_at=1 WHERE kind='release'")
        net._event(db, pid, None, "context", "v2: a struct the function uses")
    d, _ = net.local_agent("api-deepseek-chat-2")
    assert net.claim_row(d, "game")["name"] == "f0"  # the header changed: the usual order


def test_the_part_of_the_game_where_matches_happen_comes_first(tmp_path):
    """GTA San Andreas: small functions matched 19-28% of the time in most of the game, 87% in one part; a part where
    agents keep failing (another compiler, mostly) waits until the rest is done."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    good, bad, fresh = 0x100000, 0x100000 + network.REGION_BYTES, 0x100000 + 2 * network.REGION_BYTES
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "mips", 0, str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        rows = [(f"good_done_{i}", good + 0x100 * i, "matched", 1) for i in range(20)]
        rows += [(f"bad_tried_{i}", bad + 0x100 * i, "open", 3) for i in range(30)]
        rows += [("good_open", good + 0x8000, "open", 0), ("bad_twin", bad + 0x9000, "open", 0),
                 ("fresh_open", fresh, "open", 0)]
        for name, vram, status, attempts in rows:  # a twin in the failing part: it would fail the same way
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,vram,attempts,boost) VALUES(?,?,?,?,?,?,?,?)",
                       (pid, name, 40, 10, status, vram, attempts, int(name == "bad_twin")))
    order = [net.claim_row(net.local_agent(f"a{k}")[0], "game", None, "easiest")["name"] for k in range(3)]
    # the part where matches happen, then one nobody tried yet (it counts as the game's average), the failing part last
    assert order[:2] == ["good_open", "fresh_open"] and order[2].startswith("bad_"), order


def test_functions_given_up_on_go_to_the_strongest_model_at_work(tmp_path):
    """Model routing: on an N64 game free models matched 14-25% of what they claimed, stronger ones 42-77%, and
    each got the functions the others gave up on. A weak model at work beside a strong one leaves functions given up
    on 3 times to it, and the strong one takes those first; with nothing else left the weak one still gets them."""
    import time

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    strong, _ = net.local_agent("claude-sonnet-1")
    weak, _ = net.local_agent("opencode-beam-free-1")
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "mips", 1, str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        for name, n, abandons in [("hard", 4, 3), ("tried", 40, 2), ("fresh", 60, 0)]:
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,abandons) VALUES(?,?,?,?,?,?,?)",
                       (pid, name, n * 4, n, "open", "a", abandons))
        for agent, won in ((strong, 15), (weak, 2)):  # 20 claims each, this many matched
            for i in range(20):
                db.execute("INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(?,?,?,?,?)",
                           (pid, agent.id, "claim", f"old_{agent.id}_{i}", time.time()))
            for i in range(won):
                db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,matched_agent_id) "
                           "VALUES(?,?,?,?,?,?,?)", (pid, f"won_{agent.id}_{i}", 40, 10, "matched", "a", agent.id))
    assert net.claim_row(weak, "game", None, "easiest")["name"] == "fresh"  # given up on less, or not at all, first
    net.release(weak, "fresh", "", "game")
    assert net.claim_row(weak, "game", None, "easiest")["name"] == "tried"  # not "hard": that's the strong one's
    assert net.claim_row(strong, "game", None, "easiest")["name"] == "hard"  # the escalated one first
    net.release(weak, "tried", "", "game")
    net.release(strong, "hard", "", "game")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE name IN ('fresh', 'tried')")
    other, _ = net.local_agent("opencode-beam-free-2")  # the same weak model: only "hard" is left, so it gets it
    assert net.claim_row(other, "game", None, "easiest")["name"] == "hard"


def _one_function_game(tmp_path, name="piece"):
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "ppc", 1, str(tmp_path),
                          json.dumps({"kind": "rom", "rom": {}, "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": []}}))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(?,?,?,?,?,?)",
                   (pid, name, 4, 1, "open", "a"))
    return net


def test_a_function_agents_of_two_models_agree_no_c_makes_is_set_aside(tmp_path):
    """On a DS game five to seven agents in turn took each piece of another function, found no C makes it and gave
    it back with a note that changed nothing. Their verdict counts now: three agents of two models agreeing set it
    aside (status asm, the reason on its page, red in the map), and the user can give it back."""
    net = _one_function_game(tmp_path)
    status = lambda: net.store.one("SELECT status FROM functions WHERE name='piece'")["status"]  # noqa: E731

    def verdict(name):
        agent, _ = net.local_agent(name)
        net.claim_row(agent, "game", "piece")
        return net.release(agent, "piece", "a lone pop {r4, pc}: the tail of the function before", "game", not_c=True)

    assert "1 of 3 agents" in verdict("opencode-beam-free-1")
    assert "2 of 3 agents" in verdict("opencode-beam-free-2")
    assert "1 of 2 models" in verdict("opencode-beam-free-3") and status() == "open"  # one model's blind spot
    assert "set aside now" in verdict("claude-sonnet-5-5-1") and status() == "asm"
    row = net.function_map("game")[0]
    page = network.set_aside(net.store.one("SELECT * FROM functions WHERE name='piece'"))  # function_detail's
    assert row["not_c"] and page["not_c"] and "4 agents" in page["set_aside"]
    assert net.summary("game")["set_aside"] == 1 and net.summary("game")["set_aside_bytes"] == 4  # out of its %
    assert any(e["kind"] == "not_c" for e in net.store.all("SELECT kind FROM events"))
    agent, _ = net.local_agent("claude-sonnet-5-5-2")
    try:
        net.claim_row(agent, "game", "piece")
        raise AssertionError("a set-aside function handed out")
    except network.NetworkError as e:
        assert "set aside" in str(e) and "4 agents" in str(e)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open'")
    net.claim_row(agent, "game", "piece")
    try:
        net.release(agent, "piece", "", "game", not_c=True)
        raise AssertionError("a verdict without a reason")
    except network.NetworkError as e:
        assert "why" in str(e)


def test_a_set_aside_function_the_user_gives_back_stays_open(tmp_path, monkeypatch):
    """Reopen: the agents' verdicts are forgotten, and no later check sets it aside again."""
    from decompnet.app import twins

    net = _one_function_game(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='asm', target=?, meta=? WHERE name='piece'",
                   (b"\x10\x80\xbd\xe8", json.dumps({"asm_code": "3 agents found no C makes it", "not_c": {"a-1": {}}})))
    net.reopen("piece", "game")
    row = net.store.one("SELECT status, meta FROM functions WHERE name='piece'")
    assert row["status"] == "open" and json.loads(row["meta"]) == {"reopened": True}
    monkeypatch.setattr(type(net.console(net.project_row("game"))), "asm_reason", lambda self, code: "hand-written")
    assert twins.Twins(net).set_aside_asm(net.project_row("game")) == 0
    assert net.store.one("SELECT status FROM functions WHERE name='piece'")["status"] == "open"


def test_check_again_looks_at_a_project_with_what_this_version_knows(tmp_path, monkeypatch):
    """Check again (and before helpers start, once after each update): the console's work for older projects runs,
    code no compiler makes is set aside, and the project's feed says so; a project this version checked already
    isn't checked again before helpers start."""
    from decompnet import __version__

    net = _one_function_game(tmp_path)
    console = type(net.console(net.project_row("game")))
    ran = []
    monkeypatch.setattr(console, "on_start", lambda self, net, slug, log=print: ran.append(slug))
    monkeypatch.setattr(console, "asm_reason", lambda self, code: "a piece cut out of another function")
    assert net.recheck("game") == "1 more function set aside, as no C makes them" and ran == ["game"]
    assert net.cfg(net.project_row("game"))["checked"] == __version__
    assert net.store.one("SELECT status FROM functions WHERE name='piece'")["status"] == "asm"
    assert "set aside" in net.store.one("SELECT detail FROM events WHERE kind='analysis'")["detail"]
    assert net.recheck("game", only_if_updated=True) is None and ran == ["game"]  # this version did it already
    assert net.recheck("game") == "nothing new" and ran == ["game", "game"]  # the button: always


def test_agents_notes_that_agree_no_c_makes_a_function_set_it_aside(tmp_path):
    """Before release_function took a verdict, agents said so in notes: on a DS game six agents in turn wrote that a
    runtime helper with `clz` was hand-written assembly. Check again reads them: three agents of two models agreeing
    set it aside. A note about flags doesn't count ("flag-blocked" code may match with other flags), nor one model
    alone, nor the notes on a function the user gave back."""
    net = _one_function_game(tmp_path)
    with net.store.tx() as db:
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) SELECT project_id, 'flagged', 4, 1, "
                   "'open', 'a' FROM functions")
    fid = {r["name"]: r["id"] for r in net.store.all("SELECT id, name FROM functions")}

    def note(agent_name, function, text):
        agent, _ = net.local_agent(agent_name)
        with net.store.tx() as db:
            db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(1,?,?,?,0)",
                       (fid[function], agent.id, text))

    note("opencode-beam-free-1", "piece", "Hand-written ARM runtime helper, not expressible in C.")
    note("opencode-beam-free-2", "piece", "Agree: hand-written asm (inline clz). Skip.")
    assert net.not_c_from_notes("game") == 0  # one model
    note("claude-sonnet-5-5-1", "piece", "No C can produce it: the loop reuses the flags of the entry cmp.")  # the CPU's
    for i, model in enumerate(("opencode-beam-free", "opencode-glm", "claude-sonnet-5-5")):
        note(f"{model}-{i + 5}", "flagged", "Flag-blocked: no C can produce it with the project's flags.")
    assert net.not_c_from_notes("game") == 1
    row = net.store.one("SELECT status, meta FROM functions WHERE name='piece'")
    assert row["status"] == "asm" and "said in their notes" in json.loads(row["meta"])["asm_code"]
    assert net.store.one("SELECT status FROM functions WHERE name='flagged'")["status"] == "open"
    net.reopen("piece", "game")
    assert net.not_c_from_notes("game") == 0  # given back: its old notes don't set it aside again
