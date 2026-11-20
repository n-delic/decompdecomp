"""Release discipline and prerequisites. On a large x86 game, agents (mostly Claude Haiku) claimed a function and
released it seconds later without compiling (12,565 releases, median 5 s after the claim), giving reasons a compile
would have shown were none ("needs the struct for D_... first"), and sessions of two claims ended
with nothing tried. release_function now answers a release before compile_and_diff's move-on rule once with what to
do instead; a release without a compile doesn't count against the session; and a function that needs another one
done first can say so (`needs`): it waits, that one goes first."""

import json
import time

import pytest

from decompnet.network import NetworkError, stale_note, stall_budget

from .test_claims import _game, _held


def _attempts(net, agent, name, scores):
    fid = net.store.one("SELECT id FROM functions WHERE name=?", name)["id"]
    with net.store.tx() as db:
        for s in scores:
            db.execute("INSERT INTO attempts(function_id,agent_id,score,compiled,context_version,source,created_at) "
                       "VALUES(?,?,?,?,?,?,?)", (fid, agent.id, s, s is not None, 1, "int f(){}", time.time()))


def test_a_release_without_a_compile_is_pushed_back_until_it_compiles_once_more(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open"), ("g", 10, "open")])
    a, _ = net.local_agent("a")
    assert net.claim_row(a, "game", "f")["name"] == "f"
    said = net.release_function(a, "f", "needs the struct for D_80123456 first", "game")
    assert said.startswith("Not released yet: you haven't compiled f since you claimed it")
    assert "a release with no compile isn't taken" in said
    assert "ret N" not in said  # x86 advice only for x86 projects (this one is MIPS)
    assert _held(net, a) == "f"
    # asking again without a compile doesn't release it (agents just called release_function twice)
    assert net.release_function(a, "f", "same", "game").startswith("Not released yet")
    assert _held(net, a) == "f"
    time.sleep(0.01)
    _attempts(net, a, "f", [40])
    said = net.release_function(a, "f", "tried the field stores", "game")
    assert said.startswith("Released f") and "Claim the next function now" in said
    assert _held(net, a) is None


def test_asking_again_and_again_without_a_compile_is_told_to_stop(tmp_path):
    # an agent whose own instructions said it was out of tries asked 7 times in 90 seconds, each answer the full
    # advice, until the user's session quota ran out
    net, _ = _game(tmp_path, [("f", 10, "open"), ("g", 10, "open")])
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    for _ in range(2):
        assert "a release with no compile isn't taken" in net.release_function(a, "f", "out of tries", "game")
    for _ in range(2):
        said = net.release_function(a, "f", "out of tries", "game")
        assert said.startswith("Not released yet: you haven't compiled f since you claimed it")
        assert "end your session here" in said and "isn't taken" not in said
    assert _held(net, a) == "f"
    time.sleep(0.01)
    _attempts(net, a, "f", [40])  # compiled after all: the next ask goes through, as before
    assert net.release_function(a, "f", "tried it", "game").startswith("Released f")


def test_a_boundary_report_after_two_refusals_still_releases_it(tmp_path):
    # report_boundary's own answer says "release it or work on with its current size": the one-line refusal came
    # before the check for functions compiling can't help with, so that release was told "Compile it now" instead
    net, _ = _game(tmp_path, [("f", 10, "open"), ("g", 10, "open")])
    with net.store.tx() as db:  # (boundary reports: a MIPS game, and a function with an address and its bytes)
        db.execute("UPDATE projects SET arch='mips'")
        db.execute("UPDATE functions SET vram=?, target=? WHERE name='f'", (0x80001000, bytes(40)))
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    for _ in range(2):
        assert "a release with no compile isn't taken" in net.release_function(a, "f", "out of tries", "game")
    assert "release it" in net.request_boundary(a, "game", "f", "0x80001020", "zero padding after the jr ra")
    said = net.release_function(a, "f", "ends at 0x80001020, the rest is padding", "game")
    assert said.startswith("Released f")
    assert _held(net, a) is None


def test_a_session_of_refused_releases_made_no_progress(tmp_path):
    # so it fails like any session that compiled nothing: a pause between them, and after a few the helper stops
    # with the reason for the user, instead of a new session on the same function 3 s later
    from decompnet.app import agents

    net, _ = _game(tmp_path, [("f", 10, "open"), ("g", 10, "open")])
    m = agents.Manager(net, tmp_path, "http://127.0.0.1:1/mcp")  # (first: it frees claims left from before)
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    before = m._progress(a.id)
    for _ in range(3):
        net.release_function(a, "f", "out of tries", "game")
    assert m._progress(a.id) == before
    _attempts(net, a, "f", [40])
    assert m._progress(a.id) == before + 1


def test_claiming_while_a_pushed_back_release_still_holds_says_so(tmp_path):
    # an agent read its held function coming back as the queue handing back what it had just released
    net, _ = _game(tmp_path, [("f", 10, "open"), ("g", 10, "open")])
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    assert net.release_function(a, "f", "", "game").startswith("Not released yet")
    assert net.still_holding(a, None).startswith("You still hold f: it wasn't released")
    assert net.still_holding(a, "f") == ""  # asked for by name: no note
    b, _ = net.local_agent("b")
    assert net.still_holding(b, None) == ""


def test_a_release_before_the_move_on_rule_is_pushed_back_and_after_it_is_not(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open"), ("g", 10, "open")])
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    time.sleep(0.01)  # (compiles are counted from the claim's time on)
    _attempts(net, a, "f", [40])  # compiled once (68.7% and "released with the accurate state", as a live agent did)
    said = net.release_function(a, "f", "", "game")
    assert "Not released yet: 1 compile of f since you claimed it, no better in 0/3 compiles (move on at 3)." in said
    assert _held(net, a) == "f"
    b, _ = net.local_agent("b")
    net.claim_row(b, "game", "g")
    time.sleep(0.01)
    assert stall_budget(10) == 3
    _attempts(net, b, "g", [40, 41, 40, 45])  # no better in 3 in a row: compile_and_diff said to move on
    said = net.release_function(b, "g", "tried a, b, c", "game")
    assert said.startswith("Released g") and "doesn't count" not in said


def test_compile_results_and_release_function_count_the_same_way(tmp_path):
    """A compile result said "no better in your last 8" (counted across claims) while release_function
    counted the claim's compiles and refused "no better in 8 compiles in a row", then let the release through after
    one more compile; agents spent that compile to get past it. Now both say the same count, and the release goes
    through the moment the count reaches the function's number."""
    net, _ = _game(tmp_path, [("f", 10, "open")])
    a, _ = net.local_agent("a")
    fid = net.store.one("SELECT id FROM functions WHERE name='f'")["id"]
    _attempts(net, a, "f", [30, 30, 30, 30])  # an earlier claim's compiles: not this claim's
    time.sleep(0.01)
    net.claim_row(a, "game", "f")
    time.sleep(0.01)
    _attempts(net, a, "f", [40, 40])
    note = net.stall_note(fid, a.id)
    assert "Move-on count: no better in 1/3 compiles (move on at 3)." in note
    said = net.release_function(a, "f", "", "game")
    assert "no better in 1/3 compiles (move on at 3)" in said  # the very words the compile result used
    _attempts(net, a, "f", [41, 42])
    assert "⏭ Move on: no better in 3/3 compiles (move on at 3)" in net.stall_note(fid, a.id)
    assert net.release_function(a, "f", "tried x", "game").startswith("Released f")  # no compile more needed


def test_a_pushed_back_release_asked_again_goes_through_without_another_compile(tmp_path):
    net, _ = _game(tmp_path, [("f", 10, "open")])
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    _attempts(net, a, "f", [40])
    assert net.release_function(a, "f", "", "game").startswith("Not released yet")
    assert net.still_holding(a, None).startswith("You still hold f")
    assert net.release_function(a, "f", "the call to g needs its stand-in", "game").startswith("Released f")


def test_compiling_cant_help_with_a_boundary_report_waiting_for_the_user(tmp_path):
    net, pid = _game(tmp_path, [("f", 10, "open")])
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE id=?",
                   (json.dumps({"kind": "rom", "boundaries": [{"status": "open", "function": "f"}]}), pid))
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    assert net.release_function(a, "f", "padding after 0x80001234", "game").startswith("Released f")


def test_a_release_naming_what_it_needs_waits_and_that_one_goes_first(tmp_path):
    net, pid = _game(tmp_path, [("f", 10, "open"), ("g", 50, "open"), ("h", 5, "open"), ("k", 3, "open"),
                                ("done", 3, "matched")])
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    with pytest.raises(NetworkError, match="already matches"):
        net.release_function(a, "f", "", "game", needs="done")
    with pytest.raises(NetworkError, match="can't wait for itself"):
        net.release_function(a, "f", "", "game", needs="f")
    with pytest.raises(NetworkError, match="you already hold f; .*needs=\"g\""):
        net.claim_row(a, "game", "g")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET asm='bl g' WHERE name='f'")
    said = net.release_function(a, "f", "calls g with esi; its convention comes from g's definition", "game", needs="g")
    assert said.startswith("Released f") and "f now waits for g" in said
    assert _held(net, a) == "g"  # handed over (no push-back: the agent said why compiling can't help yet)
    assert "doesn't count against your session's claims" in said
    assert net.store.one("SELECT needs_id FROM waits")["needs_id"] == net.store.one(
        "SELECT id FROM functions WHERE name='g'")["id"]
    order = []
    for k in range(3):
        b, _ = net.local_agent(f"b{k}")
        order.append(net.claim_row(b, "game", None, "easiest")["name"])
    assert order == ["k", "h", "f"]  # f waits: last, even before its release cooldown counts
    brief = net.dependency_brief(net.store.one("SELECT * FROM functions WHERE name='f'"))
    assert any("Waits for g" in line for line in brief)
    assert any("Waited for by f" in line for line in net.dependency_brief(net.store.one(
        "SELECT * FROM functions WHERE name='g'")))


def test_two_functions_that_need_each_other_are_worked_on_together_not_both_held_back(tmp_path):
    # one function needs the other's call set-up, the other needs the first one's convention
    net, pid = _game(tmp_path, [("f", 10, "open"), ("g", 50, "open"), ("h", 5, "open")])
    ids = {r["name"]: r["id"] for r in net.store.all("SELECT id, name FROM functions")}
    with net.store.tx() as db:
        db.execute("INSERT INTO waits VALUES(?,?,?,?)", (ids["g"], ids["f"], None, time.time()))  # g waits for f
    a, _ = net.local_agent("a")
    net.claim_row(a, "game", "f")
    with pytest.raises(NetworkError, match=r"depend on each other.*compile_and_diff\(function=\"f, g\""):
        net.release_function(a, "f", "", "game", needs="g")
    assert _held(net, a) == "f"  # nothing released
    # a cycle already in the table (made before this check) holds neither back
    with net.store.tx() as db:
        db.execute("INSERT INTO waits VALUES(?,?,?,?)", (ids["f"], ids["g"], None, time.time()))
    b, _ = net.local_agent("b")
    assert net.claim_row(b, "game", None, "easiest")["name"] == "g"  # not last (behind h): the wait is mutual
    brief = net.dependency_brief(net.store.one("SELECT * FROM functions WHERE name='g'"))
    assert any("depend on each other" in line for line in brief)


def test_what_a_function_waits_for_is_claimed_first_and_its_match_brings_it_back(tmp_path, monkeypatch):
    from decompnet import network

    monkeypatch.setattr(network, "RELEASE_COOLDOWN", 0)
    net, pid = _game(tmp_path, [("f", 10, "open"), ("g", 50, "open"), ("h", 5, "open")])
    ids = {r["name"]: r["id"] for r in net.store.all("SELECT id, name FROM functions")}
    with net.store.tx() as db:
        db.execute("INSERT INTO waits VALUES(?,?,?,?)", (ids["f"], ids["g"], None, time.time()))
    a, _ = net.local_agent("a")
    assert net.claim_row(a, "game", None, "easiest")["name"] == "g"  # wanted: before the smaller h
    assert "Waiting for this one: f" in net.dependents_note(ids["g"])
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE id=?", (ids["g"],))
        db.execute("DELETE FROM claims")
    b, _ = net.local_agent("b")
    assert net.claim_row(b, "game", None, "easiest")["name"] == "f"  # its wait is over: first, once
    assert net.store.all("SELECT * FROM waits") == []  # the row went with the claim
    assert net.dependency_brief(net.store.one("SELECT * FROM functions WHERE name='f'")) == []


def test_notes_that_say_it_matches_are_marked_out_of_date_on_a_function_that_doesnt():
    for text in ("Already 100% - the data check failed earlier", "f is matched; nothing to do", "score 0 now",
                 "✅ MATCH with the stand-in", "objdiff 100% match",
                 # what agents wrote while a judge misread a unit's object as score 0
                 "Matched 100% with plain C (the best attempt source, unchanged), committed.",
                 "Matches 100% via compile_and_diff (committed again)",
                 "Compiles to 100% every time with the brief's source. Don't spend compiles on it."):
        assert stale_note(text, "open").startswith("[out of date"), text
        assert stale_note(text, "matched") == text
    for text in ("not 100% match yet: the call to g differs", "68.7%: reads this from the stack, ret 4",
                 "never matched, tried 4 times", "score 0.5", "Not matched; best stays 97.6% (score 433)",
                 "It never compiles to 100% with the literal"):
        assert stale_note(text, "open") == text, text


def test_longer_notes_that_say_it_matches_go_after_the_findings_on_a_function_that_doesnt():
    """An Xbox game: notes saying "matches 100%, don't spend compiles on it" (a judge had misread the unit's object)
    held three of a function's six note slots, above the finding that took it to 92%."""
    from decompnet.network import brief_notes

    fn = {"status": "open", "best_score": 1668, "n_insns": 222}
    tail = " The remaining diff the brief shows is stack slots and the order of the three quads' corner sums." * 2
    rows = [{"text": "Compiles to 100% every time with the brief's source; don't spend compiles on it." + tail,
             "created_at": 30, "name": "a"},
            {"text": "Matched 100% with plain C, committed." + tail, "created_at": 20, "name": "b"},
            {"text": "76.7% -> 92.4%: the origin is a fresh temporary at each call", "created_at": 10, "name": "c"}]
    out = brief_notes(rows, fn, best_at=10, best_text="92.4%", when=False)
    assert out[0] == "- (c) 76.7% -> 92.4%: the origin is a fresh temporary at each call"
    assert all(line.startswith(("- (a) [out of date", "- (b) [out of date")) for line in out[1:])


def test_briefs_leave_out_infrastructure_notes_show_duplicates_once_and_mark_beaten_ceilings(tmp_path):
    """Briefs: "didn't build" and "Released because its agent stopped" notes took the few note slots
    (one brief had the same "didn't build" note twice), and "plain C plateaus here" at 68.3% stayed on top after
    another agent reached 95.1%."""
    net, _ = _game(tmp_path, [("f", 100, "open")])
    a, _ = net.local_agent("a")
    fid = net.store.one("SELECT id FROM functions WHERE name='f'")["id"]
    now = time.time()
    notes = [  # oldest first
        (now - 500, "plain C plateaus here at 68.3%: register allocation, not reachable"),
        (now - 400, "u_unit didn't build before this change either, so it was left for now: FAILED: build.ninja"),
        (now - 390, "u_unit didn't build before this change either, so it was left for now: FAILED: build.ninja"),
        (now - 300, "Released because its agent stopped (You've hit your session limit); its attempts are kept."),
        (now - 200, "Failed to authenticate: OAuth session expired"),
        (now - 100, "the loop counter is a short; try   the do-while form"),
        (now - 90, "the loop counter is a short; try the do-while form"),
        (now - 10, "after the 95% try: the call to g still pushes instead of using esi"),
    ]
    with net.store.tx() as db:
        for t, text in notes:
            db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(1,?,?,?,?)",
                       (fid, a.id, text, t))
        db.execute("UPDATE functions SET best_score=490 WHERE id=?", (fid,))  # 95.1% of 100 instructions
        db.execute("INSERT INTO attempts(function_id,agent_id,score,compiled,source,created_at) VALUES(?,?,?,?,?,?)",
                   (fid, a.id, 490, 1, "x", now - 50))
    lines = net.notes_for_brief(net.store.one("SELECT * FROM functions WHERE id=?", fid))
    section = "\n".join(lines)
    assert "didn't build" not in section and "OAuth" not in section and "agent stopped" not in section
    assert lines[-1].startswith("- (3 notes about builds that broke or agents that stopped left out")
    assert sum("do-while" in ln for ln in lines) == 1 and "[noted 2 times]" in section
    assert "95% try" in lines[0]  # newest first
    assert lines[-2].startswith("- (a, ") and "(superseded: best is now 490) plain C plateaus" in lines[-2]


def test_a_note_newer_than_the_best_or_better_than_it_isnt_superseded():
    from decompnet.network import brief_notes

    fn = {"status": "open", "best_score": 1000, "n_insns": 100}  # 90%
    rows = [{"text": "reached 92.5% with the cast", "created_at": 5, "name": "a"},
            {"text": "plateaus at 85%", "created_at": 50, "name": "b"},  # written after the best: still news
            {"text": "stuck around 80% (score 2000)", "created_at": 1, "name": "c"}]
    out = brief_notes(rows, fn, best_at=10, best_text="90.0%", when=False)
    assert out[0] == "- (a) reached 92.5% with the cast"
    assert out[1] == "- (b) plateaus at 85%"
    assert out[2] == "- (c) (superseded: best is now 90.0%) stuck around 80% (score 2000)"


def test_prompts_bound_compiled_claims_and_allow_context_limited_sessions():
    from decompnet.app import tools

    for prompt in (tools.WORK_PROMPT, tools.FORK_PROMPT):
        text = " ".join(prompt.format(title="t", count=2, compiler_guide="g", platform="p", guides="g",
                                      budget="b", what="w").split())
        assert "Work on up to 2 compiled claims (a batch is one)" in text
        assert "The claim count is a maximum, not a quota" in text
        assert "Continue with the next eligible function while session capacity remains" in text
        assert "Stop earlier when the session turn/context allowance is nearly used" in text
        assert "on request" in text
        assert "needs=" in text


def test_a_brief_leaves_out_notes_that_only_say_an_open_function_matches(tmp_path):
    """A false 100% (before #59) left "Already 100% match on claim, no work needed" on 12 big open functions of a
    DS game, and each agent took the last one's note as its reason to skip it. Such a note says nothing else, so a
    brief leaves it out; a longer note keeps its findings, marked out of date."""
    net, _ = _game(tmp_path, [("f", 1000, "open")])
    a, _ = net.local_agent("a")
    fid = net.store.one("SELECT id FROM functions WHERE name='f'")["id"]
    long = "Already 100% per brief. " + "Helpers decoded: func_0206d4f8(2 args) for bl 0xffffa150; " * 4
    with net.store.tx() as db:
        for k, text in enumerate(("Already 100% match on claim, no work needed.", long)):
            db.execute("INSERT INTO notes(project_id,function_id,agent_id,text,created_at) VALUES(1,?,?,?,?)",
                       (fid, a.id, text, time.time() + k))
    section = "\n".join(net.notes_for_brief(net.store.one("SELECT * FROM functions WHERE id=?", fid)))
    assert "no work needed" not in section and "1 note saying it already matches left out" in section
    assert "[out of date" in section and "func_0206d4f8" in section


@pytest.mark.parametrize('changed', ['header', 'compiler', 'boundary'])
def test_handoff_briefs_use_measured_scores_and_flag_older_inputs(tmp_path, changed):
    from decompnet.app.claim_state import history

    net, pid = _game(tmp_path, [('f', 10, 'open')])
    first, _ = net.local_agent('first')
    second, _ = net.local_agent('second')
    cfg = {'kind': 'rom', 'compiler': {'toolchain': 'example', 'flags': ['-O2']}}
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,1,'typedef int Integer;', '')",
                   (pid,))
    fn = net.claim_row(first, 'game', 'f')
    net._record(fn, first, 90, 'int f(void) { return 90; }', 1)
    net._record(fn, first, 40, 'int f(void) { return 40; }', 1)
    text = ('Difference: target uses an unsigned comparison.\n'
            'Tried volatile, which added two loads.\n'
            'Next step: test the unsigned comparison.')
    assert net.release_function(first, 'f', text, 'game', move_on=True).startswith('Released f')
    net.add_note(second, 'game', 'The final store uses a halfword.', 'f')
    fn = net._function('f', 'game')
    notes = net.notes_for_brief(fn, when=False)
    assert notes[0].startswith('- (first; saved score 90 → 40) Next step: test the unsigned comparison.')
    assert 'Tried volatile' in notes[0]
    assert 'saved score 90 → 40' in '\n'.join(history(net, fn))
    assert net.store.one('SELECT text FROM notes WHERE agent_id=?', first.id)['text'] == text
    with net.store.tx() as db:
        if changed == 'header':
            db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,2,'typedef unsigned Integer;', '')",
                       (pid,))
        elif changed == 'compiler':
            cfg['compiler']['flags'] = ['-O1']
            db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
        else:
            db.execute('UPDATE functions SET size=size+4 WHERE id=?', (fn['id'],))
    fn = net._function('f', 'game')
    notes = net.notes_for_brief(fn, when=False)
    assert 'The final store' in notes[0]
    expected = {'header': 'headers', 'compiler': 'compiler or build settings', 'boundary': 'function target or boundaries'}
    assert f'[Inputs changed since testing ({expected[changed]});' in notes[1]
    assert 'recheck before relying on it' in '\n'.join(history(net, fn))


def test_release_tool_may_omit_notes_and_preserves_attempts(tmp_path):
    import anyio

    from decompnet.agents.api import api_tool_schemas
    from decompnet.app.tools import _release_with_experiment, build_mcp

    net, _ = _game(tmp_path, [('f', 10, 'open')])
    agent, _ = net.local_agent('worker')
    names = ['release_function', 'release_and_continue']
    for schema in api_tool_schemas(names=names):
        assert 'notes' not in schema['function']['parameters']['required']
    definitions = anyio.run(build_mcp(net, lambda ctx: (agent, 'game')).list_tools)
    for tool in definitions:
        if tool.name in names:
            assert 'notes' not in tool.input_schema.get('required', [])
    assert net.claim_row(agent, 'game', 'f')
    _attempts(net, agent, 'f', [40])
    result = _release_with_experiment(net, agent, 'game', 'f')
    if _held(net, agent):
        assert result.startswith('Not released yet')
        result = _release_with_experiment(net, agent, 'game', 'f')
    assert result.startswith('Released f')
    assert not net.store.one('SELECT 1 FROM notes')
    assert net.store.one('SELECT score FROM attempts')['score'] == 40


def test_room_handoff_scores_are_advisory_and_can_come_from_guest_attempts(tmp_path):
    from decompnet import agent_notes
    from decompnet.release_receipts import transport_release

    net, _ = _game(tmp_path, [('f', 10, 'open')])
    agent, _ = net.local_agent('guest')
    fn = net.claim_row(agent, 'game', 'f')
    raw = [60, 40, 'a' * 64]
    assert agent_notes.validate_room_handoff([60, 2**64, 'a' * 64]) is None
    assert agent_notes.validate_room_handoff([60, 40, 'bad key']) is None
    assert transport_release(net, agent, 'f', 'Next step: try an unsigned comparison.', 'game',
                             room_evidence=agent_notes.validate_room_handoff(raw)).startswith('Released f')
    row = net.store.one('SELECT score_before,score_after,handoff_inputs FROM notes')
    assert list(row) == raw
    assert not net.store.one('SELECT 1 FROM attempts')
    assert net._function(fn['name'], 'game')['best_score'] is None


def test_project_handoff_inputs_follow_headers_and_flags_but_ignore_candidate_body(tmp_path):
    from decompnet.agent_notes import project_inputs
    from decompnet.forks.project import ForkProject, Unit

    net, _ = _game(tmp_path, [('f', 10, 'open')])
    fn = net._function('f', 'game')
    project = ForkProject(tmp_path, create_state=False)
    source = tmp_path / 'f.c'
    source.write_text('#include "types.h"\nint f(void) { return 0; }')
    header = tmp_path / 'types.h'
    header.write_text('typedef int Value;')
    makefile = tmp_path / 'Makefile'
    makefile.write_text('CFLAGS := -O2')
    unit = Unit('f', None, None, 'f.c', False, None)
    original = project_inputs(project, unit, fn)
    source.write_text('#include "types.h"\nint f(void) { return 1; }')
    assert project_inputs(project, unit, fn) == original
    header.write_text('typedef unsigned Value;')
    with_new_header = project_inputs(project, unit, fn)
    assert with_new_header != original
    makefile.write_text('CFLAGS := -O1')
    assert project_inputs(project, unit, fn) != with_new_header
