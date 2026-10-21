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


def _game(tmp_path, functions):
    """A ROM-kind project with these (name, n_insns, status) functions."""
    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("game", "Game", "", "ppc", 1, str(tmp_path), json.dumps({"kind": "rom"}))).lastrowid
        for name, n, status in functions:
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(?,?,?,?,?,?)",
                       (pid, name, n * 4, n, status, "a"))
    return net, pid


def _held(net, agent):
    row = net.store.one("SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=?", agent.id)
    return row and row["name"]


def test_helpers_reused_by_name_claim_again_after_the_app_restarts(tmp_path):
    """On a demo fork four Haiku helpers found nothing to claim among ten open functions, while a new Opus helper claimed
    at once. Their names (reused across runs) had released those functions in earlier runs, and the rest were still
    held by helpers that died when the app was updated."""
    import time

    net, pid = _game(tmp_path, [("done", 4, "matched"), ("f1", 4, "open"), ("f2", 6, "open"), ("f3", 8, "open")])
    haiku1, _ = net.local_agent("claude-haiku-1")
    haiku2, _ = net.local_agent("claude-haiku-2")
    chat = net.authenticate(net.register_agent("elsewhere", "you", origin="cli"))  # an agent over MCP, on its own
    for name in ("f1", "f2"):  # given up on by haiku-1 in an earlier run
        assert net.claim_row(haiku1, "game", name)["name"] == name
        net.release(haiku1, name, "too hard for me", "game")
    with net.store.tx() as db:
        db.execute("UPDATE events SET created_at=created_at-3600 WHERE kind='release'")
    assert net.claim_row(haiku2, "game", "f3")["name"] == "f3"  # then the app stopped with haiku-2 on f3
    assert net.claim_row(chat, "game", "f2")["name"] == "f2"  # the user's own agent, which runs on its own

    assert net.release_local_claims("the app restarted") == ["f3"]  # the app starting again
    assert _held(net, haiku2) is None and _held(net, chat) == "f2"  # an agent elsewhere keeps its lease
    note = net.store.one("SELECT text FROM notes WHERE function_id=(SELECT id FROM functions WHERE name='f3')")
    assert "its agent stopped (the app restarted)" in note["text"]
    assert net.store.one("SELECT abandons FROM functions WHERE name='f3'")["abandons"] == 0  # nobody gave up on it

    assert net.claim_row(haiku1, "game", None)["name"] == "f3"  # the function it hasn't tried first
    net.release(haiku1, "f3", "", "game")
    # what it released in an earlier run comes after all, rather than nothing
    assert net.claim_row(haiku1, "game", None)["name"] == "f1"
    assert time.time() - net.store.one("SELECT MAX(created_at) AS t FROM events WHERE kind='freed'")["t"] < 60


def test_an_agent_doesnt_get_back_what_it_just_released_until_its_run_ends(tmp_path):
    net, _ = _game(tmp_path, [("x", 4, "open")])
    a, _ = net.local_agent("a")
    assert net.claim_row(a, "game", None)["name"] == "x"
    net.release(a, "x", "", "game")
    assert net.claim_row(a, "game", None) is None
    net.release_stopped([a.id], "it was stopped")  # its helper stopped: the next run starts afresh
    assert net.claim_row(a, "game", None)["name"] == "x"


def test_nothing_to_claim_says_who_holds_what_and_what_you_set_aside(tmp_path):
    net, _ = _game(tmp_path, [("mine", 4, "open"), ("theirs", 6, "open"), ("done", 8, "matched")])
    a, _ = net.local_agent("a")
    b, _ = net.local_agent("b")
    net.claim_row(a, "game", "mine")
    net.release(a, "mine", "", "game")
    net.claim_row(b, "game", "theirs")
    said = net.claim(a, "game")
    assert "2 functions are open" in said, said
    assert "theirs (b, 29 min left)" in said, said
    assert "1 you released yourself" in said and 'function="mine"' in said, said
    assert net.claim_row(a, "game", "mine")["name"] == "mine"  # by name: at once
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched'")
        db.execute("DELETE FROM claims")
    assert "every function matches" in net.claim(a, "game")
