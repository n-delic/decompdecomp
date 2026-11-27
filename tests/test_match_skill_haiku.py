"""haiku.py, the small-model matching loop: `next` claims and shows one screen, `try` judges under every profile and
saves a match under another profile for that function only, `done` keeps the work and releases. The app and its judge
are stand-ins; match_brief (what the judge worker reads) runs on a small real project."""
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from decompnet import hosted_judge, toolchains
from decompnet.app import compiler_experiments, match_brief
from tests.test_hosted import project
from tests.test_match_skill_setup import load

haiku = load("haiku")
OWN = {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"], "uses": 5, "own": True}
OTHER = {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O1"], "uses": 9, "own": False}
FUNCTION = {"name": "f1", "vram": 0x80001100, "target": "0103e008"}


@pytest.mark.parametrize("source,reason", [
    ("void f(void) { do { a = 1; } while (0); }", "fence"),
    ("void f(void) { s32 __repair_x = 0; g = __repair_x; }", "__repair_"),
    ("void f(void) { volatile s32 x = 0; g = x; }", "volatile"),
    ("void f(void) { s32 sp[4]; g = 1; }", "padding array"),
    ("void f(void) { char pad[0x10]; use(pad); }", "padding array"),
])
def test_scaffolding_is_refused(source, reason):
    assert any(reason in fault for fault in haiku.faults(source))


def test_ordinary_c_is_not_refused():
    source = """/* do { } while (0) in a comment */
    typedef struct { u8 pad[0x10]; s32 x; } T;
    extern s32 table[];
    void f(T *t) { s32 values[3]; values[0] = t->x; do { g("volatile"); } while (values[0]--); table[0] = 1; }"""
    assert haiku.faults(source) == []


FENCED = "void f(void) { do { g = __repair_a; } while (0); }"


def test_scaffolding_the_start_already_has_may_stay_but_none_may_be_added():
    allowed = haiku.inherited([FENCED, None, ""])
    assert haiku.faults(FENCED.replace("g =", "h = 1; g ="), allowed) == []
    assert any("fence" in f for f in haiku.faults(FENCED + " void g2(void) { do {} while (0); }", allowed))
    assert any("__repair_" in f for f in haiku.faults(FENCED.replace("g =", "__repair_b = 1; g ="), allowed))
    assert any("volatile" in f for f in haiku.faults("volatile s32 x; " + FENCED, allowed))


def revision(source):
    return hashlib.sha256(source.encode()).hexdigest()[:20]


class Gateway:
    calls, held = [], True
    current = "// saved"  # the session's candidate in the app
    notice = []  # texts the gateway puts on top of the next get_claims replies

    def __init__(self, *args):
        pass

    def start(self):
        pass

    def call(self, tool, arguments, wait=None):
        Gateway.calls.append((tool, arguments))
        if tool == "get_claims":
            reply = json.dumps({"claims": [{"function": "f1", "claim_token": "tok"}] if Gateway.held else []})
            return Gateway.notice.pop(0).replace("REPLY", reply) if Gateway.notice else reply
        if tool == "get_candidate":
            return (f"note\nclaim_token: tok\nCandidate revision {revision(Gateway.current)}\n```c\n"
                    f"{Gateway.current}\n```\nmore")
        if tool == "compiler_experiments":
            assert arguments["revision"] == revision(Gateway.current)
            return "1: score 7; diagnostic; trial_id=abc123; compiler=x"
        if tool == "release_function":
            Gateway.held = False
        if tool == "suggest":
            App.status = "matched"
        return f"{tool} ok"


class App:
    status, best = "open", 50

    def __init__(self, home=None):
        pass

    def function(self, slug, name, quick=False):
        return {"status": App.status, "best": App.best}


class Worker:
    """Scores by a marker in the source: `own=N other=N`."""

    def __init__(self, info):
        pass

    def score(self, function, profile, source, rows=False):
        words = dict(word.split("=") for word in source.split() if "=" in word)
        value = words.get("own" if profile["own"] else "other")
        return {"score": None if value is None else int(value), "detail": "boom", "diff": "- a | + b"}

    def close(self):
        pass


@pytest.fixture
def session(tmp_path, monkeypatch):
    Gateway.calls, Gateway.held, App.status, App.best = [], True, "open", 50
    Gateway.current, Gateway.notice = "// saved", []
    sent, stopped = [], []
    root = tmp_path / "dd-small"
    root.mkdir()
    info = {"holder": "small", "slug": "game", "function": "f1", "home": "", "gateway_url": "u", "token": "secret-tok"}
    (root / "connection.json").write_text(json.dumps(info))
    haiku.write_state(root, {"function": FUNCTION, "profiles": [OWN, OTHER], "tries": 0, "best": {}, "submitted": []})
    monkeypatch.setattr(haiku.setup, "TMP", tmp_path)
    monkeypatch.setattr(haiku.setup, "Gateway", Gateway)
    monkeypatch.setattr(haiku.setup, "submit", lambda holder, source, function="", again="": sent.append(source) or "judged")
    monkeypatch.setattr(haiku.setup, "stop_session", lambda holder: stopped.append(holder) or 0)
    monkeypatch.setattr(haiku.judge, "App", App)
    monkeypatch.setattr(haiku, "Worker", Worker)
    monkeypatch.setattr(haiku, "VERIFY_SECONDS", 0)
    monkeypatch.setattr(haiku.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(haiku, "library_chain", lambda root, info, name, profile, path: None)

    def write(text):
        path = root / "work.c"
        path.write_text(text)
        return str(path)
    return SimpleNamespace(root=root, write=write, sent=sent, stopped=stopped)


def tools():
    return [tool for tool, _ in Gateway.calls]


def test_try_submits_only_what_beats_the_saved_score(session, capsys):
    haiku.main(["try", "small", session.write("// own=60 other=70")])
    assert session.sent == [] and "not submitted" in capsys.readouterr().out
    haiku.main(["try", "small", session.write("// own=40 other=70")])
    assert session.sent == ["// own=40 other=70"]
    out = capsys.readouterr().out
    assert "try 2 of" in out and "secret-tok" not in out


def test_an_exact_candidate_under_another_profile_is_suggested_for_this_function_only(session, capsys):
    source = "// own=40 other=0"
    assert haiku.main(["try", "small", session.write(source)]) == 0
    assert Gateway.calls == [("suggest", {"function": "f1", "compiler": "gcc-2.7.2-kmc", "flags": "-O1",
                                          "source": source,
                                          "text": "Exact under gcc-2.7.2-kmc -O1 for f1 only."})]
    assert "MATCHED" in capsys.readouterr().out and session.sent == []
    assert haiku.main(["done", "small"]) == 0
    assert session.stopped == ["small"] and "release_function" not in tools()


def test_a_refused_source_is_never_judged(session):
    with pytest.raises(SystemExit, match="fence"):
        haiku.main(["try", "small", session.write("void f(void) { do { } while (0); } // own=0")])
    assert session.sent == [] and Gateway.calls == []


def test_a_function_the_app_renamed_after_matching_is_still_seen_as_matched(session, monkeypatch, capsys):
    class Renamed(App):
        def function(self, slug, name, quick=False):
            if name == "0x80010000":
                return {"id": 5, "name": "fixedSqrt", "status": App.status, "best": 0}
            return {"HTTP": 400} if App.status == "matched" else {"status": App.status, "best": App.best}

    def submit(holder, source, function="", again=""):
        App.status = "matched"
        return "judged"

    monkeypatch.setattr(haiku.judge, "App", Renamed)
    monkeypatch.setattr(haiku.setup, "submit", submit)
    info = json.loads((session.root / "connection.json").read_text())
    (session.root / "connection.json").write_text(json.dumps(dict(info, address="0x80010000", function_id=5)))
    assert haiku.main(["try", "small", session.write("// own=0 other=70")]) == 0
    assert "MATCHED: the app records" in capsys.readouterr().out
    assert haiku.main(["done", "small"]) == 0
    assert "add_note" not in tools() and "RESULT f1: MATCHED" in capsys.readouterr().out


def start_from(session, source):
    state = haiku.read_state(session.root)
    haiku.write_state(session.root, dict(state, inherited=haiku.inherited([source])))


def test_an_improvement_that_keeps_inherited_scaffolding_is_submitted(session):
    start_from(session, FENCED)
    source = FENCED + " // own=40 other=70"
    haiku.main(["try", "small", session.write(source)])
    assert session.sent == [source]


@pytest.mark.parametrize("scores", ["own=0 other=70", "own=90 other=0"])
def test_an_exact_candidate_with_inherited_scaffolding_is_saved_and_said(session, capsys, monkeypatch, scores):
    def submit(holder, source, function="", again=""):  # the app records an own-profile exact as matched
        session.sent.append(source)
        App.status = "matched"
        return "judged"
    monkeypatch.setattr(haiku.setup, "submit", submit)
    start_from(session, FENCED)
    source = f"{FENCED} // {scores}"
    assert haiku.main(["try", "small", session.write(source)]) == 0
    assert "keeps scaffolding it started with (fence, repair)" in capsys.readouterr().out
    assert session.sent == [source] or dict(Gateway.calls)["suggest"]["source"] == source
    assert haiku.main(["done", "small"]) == 0
    assert "MATCHED (keeps inherited scaffolding: fence, repair)" in capsys.readouterr().out


def test_the_screen_lets_the_saved_best_be_the_start(tmp_path):
    brief = {"name": "f1", "n_insns": 4, "asm": "jr $ra", "best": FENCED + " // own=60", "draft": "// own=80",
             "best_score": 60, "profiles": [OWN], "declarations": [], "neighbours": [], "trials": []}
    out = []
    _, _, allowed = haiku.screen(tmp_path, {"holder": "small", "slug": "game"}, brief, Worker({}), write=out.append)
    assert allowed == {"fence": 1, "repair:__repair_a": 1}
    assert "`try` accepts what is already there" in "\n".join(out)


def test_done_keeps_the_near_miss_as_a_trial_writes_the_note_and_releases_with_the_token(session, capsys):
    # a worse own-profile score is never submitted, but the closest under another profile is recorded as a trial
    haiku.main(["try", "small", session.write("// own=90 other=7")])
    assert session.sent == []
    assert haiku.main(["giveup", "small", "--note", "a register swap is left"]) == 0
    assert session.sent == []
    assert tools() == ["get_candidate", "compiler_experiments", "add_note", "get_claims", "release_function",
                       "get_claims"]
    trial = dict(Gateway.calls)["compiler_experiments"]
    assert trial["revision"] == revision("// saved")
    assert trial["trials"] == [{"compiler": "gcc-2.7.2-kmc", "flags": "-O1", "old": "// saved",
                                "new": "// own=90 other=7"}]
    assert "suggest" not in tools()
    release = dict(Gateway.calls)["release_function"]
    assert release["claim_token"] == "tok" and release["move_on"] is True
    assert "best 7 under gcc-2.7.2-kmc -O1" in release["notes"] and "register swap" in release["notes"]
    assert "trial abc123" in release["notes"]
    assert session.stopped == ["small"] and "not matched" in capsys.readouterr().out


def chain(monkeypatch, result):
    asked = []

    def run(root, info, name, profile, path):
        asked.append((name, profile["flags"], path.read_text()))
        return result
    monkeypatch.setattr(haiku, "library_chain", run)
    return asked


def test_done_saves_the_match_the_repair_library_finds_from_a_near_miss(session, monkeypatch, capsys):
    asked = chain(monkeypatch, "// own=90 other=0")

    def apply(self, tool, arguments, wait=None, real=Gateway.call):
        if tool == "suggest":
            App.status = "matched"
        return real(self, tool, arguments)
    monkeypatch.setattr(Gateway, "call", apply)
    haiku.main(["try", "small", session.write("// own=90 other=7")])
    assert haiku.main(["done", "small"]) == 0
    assert asked == [("f1", ["-O1"], "// own=90 other=7")]
    assert dict(Gateway.calls)["suggest"]["source"] == "// own=90 other=0"
    assert "compiler_experiments" not in tools() and "release_function" not in tools()
    assert "RESULT f1: MATCHED" in capsys.readouterr().out


def test_done_keeps_the_closer_source_the_repair_library_reaches(session, monkeypatch):
    chain(monkeypatch, "// own=90 other=3")
    haiku.main(["try", "small", session.write("// own=90 other=7")])
    haiku.main(["done", "small"])
    assert dict(Gateway.calls)["compiler_experiments"]["trials"][0]["new"] == "// own=90 other=3"
    assert "best 3 under" in dict(Gateway.calls)["release_function"]["notes"]


def test_scaffolding_the_library_adds_is_not_taken(session, monkeypatch):
    chain(monkeypatch, "// own=90 other=0\nvoid f(void) { do { } while (0); }")
    haiku.main(["try", "small", session.write("// own=90 other=7")])
    haiku.main(["done", "small"])
    assert "suggest" not in tools()
    assert "best 7 under" in dict(Gateway.calls)["release_function"]["notes"]


def test_a_far_near_miss_is_not_chained(session, monkeypatch):
    asked = chain(monkeypatch, "// own=900 other=0")
    haiku.main(["try", "small", session.write(f"// own=900 other={haiku.CHAIN_NEAR + 1}")])
    haiku.main(["done", "small"])
    assert asked == []


def test_done_with_nothing_tried_only_ends_the_session(session):
    assert haiku.main(["done", "small"]) == 0
    assert session.stopped == ["small"] and Gateway.calls == []


def test_a_near_miss_no_better_than_the_kept_one_is_not_recorded_again(session):
    state = haiku.read_state(session.root)
    haiku.write_state(session.root, dict(state, kept=7))
    haiku.main(["try", "small", session.write("// own=90 other=7")])
    haiku.main(["done", "small"])
    assert "compiler_experiments" not in tools()


def test_a_refused_trial_keeps_the_near_miss_in_the_note(session, monkeypatch):
    def refuse(self, tool, arguments, wait=None, real=Gateway.call):
        if tool == "compiler_experiments":
            raise haiku.setup.McpError("not offered")
        return real(self, tool, arguments)
    monkeypatch.setattr(Gateway, "call", refuse)
    haiku.main(["try", "small", session.write("// own=90 other=7")])
    assert haiku.main(["done", "small"]) == 0
    assert "// own=90 other=7" in dict(Gateway.calls)["release_function"]["notes"]


def test_done_survives_a_notice_or_a_broken_reply_on_the_claim_check(session):
    # the gateway puts an acknowledged release's answer on top of the next reply
    Gateway.notice = ["not json", "decompdecomp has now finished your earlier add_note call ({a}): ok\n\nREPLY",
                      "release_function accepted {\n", "REPLY"]
    haiku.main(["try", "small", session.write("// own=90 other=95")])
    assert haiku.main(["done", "small"]) == 0
    assert session.stopped == ["small"] and "release_function" in tools()
    assert not Gateway.notice


def test_the_screen_shows_the_near_miss_and_the_twin(tmp_path):
    brief = {"name": "f1", "n_insns": 4, "asm": "jr $ra", "best": "// own=60 other=70", "draft": "// own=80",
             "best_score": 60, "profiles": [OWN, OTHER], "declarations": [], "neighbours": [],
             "trials": [{"score": 3, "toolchain": "gcc-2.7.2-kmc", "flags": ["-O1"], "source": "// do other=1\n// other=7"}],
             "twin": {"name": "f9", "similarity": 0.9, "toolchain": "gcc-2.7.2-kmc", "flags": ["-O1"],
                      "source": "void f9(void) {}"}}
    out = []
    results, kept, _ = haiku.screen(tmp_path, {"holder": "small", "slug": "game"}, brief, Worker({}), write=out.append)
    text = "\n".join(out)
    assert "near-miss kept from an earlier session: score 3" in text and "void f9(void) {}" in text
    assert "MATCHED twin f9: 90%" in text and "(twin)" in text
    assert f"cp {tmp_path}/start/near.c {tmp_path}/work.c" in text and kept == 7
    assert (tmp_path / "start" / "twin.c").read_text() == "void f9(void) {}"


def test_the_brief_offers_trials_and_the_matched_twin_and_picks_fresh_functions_first(tmp_path):
    net = project(tmp_path)
    pid = net.project_row("game")["id"]
    store = net.project_store(pid)
    with store.tx() as db:
        db.execute("UPDATE functions SET seed='void ' || name || '(void) {}' WHERE project_id=?", (pid,))
        db.execute("UPDATE functions SET target=X'27BDFFE8AFBF00140C000000000000008FBF001403E00008' "
                   "WHERE name IN ('f8','f29')")
        db.execute("UPDATE functions SET status='matched', matched_source='void f29(void) {}' WHERE name='f29'")
        db.execute("UPDATE functions SET best_score=1, best_source='x' WHERE name IN ('f5','f6','f7')")
        db.execute("UPDATE functions SET name='osThing', best_score=0 WHERE name='f6'")
        db.execute("UPDATE functions SET abandons=3 WHERE name='f7'")
        ids = {row["name"]: row["id"] for row in db.execute("SELECT id, name FROM functions").fetchall()}
        db.execute("INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(?,?,?,?,?)",
                   (pid, 999, "release", "f5", time.time()))
    items = match_brief.candidates(net, "game", max_insns=10)
    names = [item["name"] for item in items]
    assert "osThing" not in names and names.index("f7") < names.index("f5")
    assert next(item for item in items if item["name"] == "f5")["worked"] > 0
    fn = store.one("SELECT * FROM functions WHERE name='f8'")
    compiler_experiments.save_trial(net, fn, "t1", {
        "agent": 1, "source": "void f8(void) {}", "compiler": "gcc-2.7.2-kmc", "flags": "-O1",
        "result": {"score": 12, "exact": False}})
    assert ids["f8"] == fn["id"]
    brief = match_brief.brief(net, "game", "f8")
    assert brief["trials"] == [{"score": 12, "toolchain": "gcc-2.7.2-kmc", "flags": ["-O1"],
                                "source": "void f8(void) {}"}]
    assert brief["twin"]["name"] == "f29" and brief["twin"]["similarity"] == 1.0
    assert brief["twin"]["source"] == "void f29(void) {}"
    if toolchains.installed("gcc-2.7.2-kmc"):
        assert any(p["flags"] == ["-O1"] for p in brief["profiles"])


def test_among_lately_worked_functions_the_least_given_up_come_first(tmp_path):
    net = project(tmp_path)
    pid = net.project_row("game")["id"]
    with net.project_store(pid).tx() as db:
        db.execute("UPDATE functions SET seed='void ' || name || '(void) {}' WHERE project_id=?", (pid,))
        db.execute("UPDATE functions SET best_score=1, best_source='x' WHERE name IN ('f5','f7','f8')")
        db.execute("UPDATE functions SET abandons=8 WHERE name='f5'")
        db.execute("UPDATE functions SET abandons=1 WHERE name IN ('f7','f8')")
        for name, ago in (("f5", 300), ("f7", 100), ("f8", 200)):
            db.execute("INSERT INTO events(project_id,agent_id,kind,detail,created_at) VALUES(?,?,?,?,?)",
                       (pid, 999, "release", name, time.time() - ago))
    items = match_brief.candidates(net, "game", max_insns=10)
    names = [item["name"] for item in items if item["best_score"] is not None]
    assert names == ["f8", "f7", "f5"]
    ranked = haiku.rank(Worker({}), [dict(item, profiles=[OWN], matched_near=0) for item in items
                                     if item["name"] in names])
    assert ranked == names


def test_the_worker_lists_candidates_and_briefs_a_function_without_writing(tmp_path):
    net = project(tmp_path)
    pid = net.project_row("game")["id"]
    with net.project_store(pid).tx() as db:
        db.execute("UPDATE functions SET seed='void ' || name || '(void) {}' WHERE project_id=?", (pid,))
        db.execute("UPDATE functions SET best_score=90, best_source='void f5(void) {}' WHERE name='f5'")
        db.execute("UPDATE functions SET matched_source='s32 f29(s32 a) {\n    return a;\n}' WHERE name='f29'")
    items = match_brief.candidates(net, "game", max_insns=10)
    assert items[0]["name"] == "f5" and items[0]["best_score"] == 90
    assert all(item["n_insns"] <= 10 and item["profiles"][0]["own"] for item in items)
    brief = hosted_judge.match_brief_report(net, {"slug": "game", "name": "f8"})
    assert brief["draft"] and brief["asm"] and brief["profiles"][0]["flags"] == ["-O2", "-G0"]
    assert net.project_store(pid).one("SELECT seed FROM functions WHERE name='f8'")["seed"] == "void f8(void) {}"
    assert match_brief.signature("s32 f29(s32 a) {\n return a;\n}", "f29") == "s32 f29(s32 a)"
