"""The closest attempt under another compiler profile is kept and handed to the next worker (app/profile_attempts.py):
an -O1 near-miss of an -O2 function was lost when its agent gave up, and the next one started from a poor best."""
import json
from types import SimpleNamespace

from decompnet.app import agent_tokens, claim_state, match_brief, profile_attempts
from decompnet.app.engine.compilers import CompilerMixin
from tests.test_hosted import project

TC = "gcc-2.7.2-kmc"
NEAR = "void f8(void) { near(); }"


def setup(tmp_path, monkeypatch, scores):
    """A real small ROM project; the compiler is a stand-in scoring each (flags, source) from `scores`."""
    net = project(tmp_path)
    proj = net.project_row("game")
    with net.project_store(proj["id"]).tx() as db:
        db.execute("UPDATE functions SET best_score=90, best_source='void f8(void) {}' WHERE name='f8'")
    monkeypatch.setattr(net, "_toolchain_named", lambda cfg, name: name)

    def judge(proj, fn, toolchain, flags, source):
        score = scores[" ".join(flags), source]
        return score == 0, SimpleNamespace(score=score, percent=50.0, render=lambda **_: "rows"), ""

    monkeypatch.setattr(net, "_judge_with", judge)
    agent, _ = net.local_agent("a")
    return net, proj, agent


def test_the_closest_other_profile_compile_is_kept_without_changing_anything(tmp_path, monkeypatch):
    scores = {("-O1", NEAR): 3, ("-O1", "worse"): 40, ("-O2 -G0", "own"): 1}
    net, proj, agent = setup(tmp_path, monkeypatch, scores)
    config = proj["config"]
    net.try_compiler(agent, "game", "f8", TC, "-O1", NEAR)
    net.try_compiler(agent, "game", "f8", TC, "-O1", "worse")  # not closer: the near one stays
    net.try_compiler(agent, "game", "f8", TC, "-O2 -G0", "own")  # the function's own profile is not "another"
    fn = net._function("f8", "game")
    assert profile_attempts.kept(net, fn) == {"score": 3, "toolchain": TC, "flags": ["-O1"], "source": NEAR}
    assert (fn["best_score"], fn["best_source"], fn["status"]) == (90, "void f8(void) {}", "open")
    assert net.project_row("game")["config"] == config
    assert net.project_store(proj["id"]).one("SELECT COUNT(*) AS n FROM attempts")["n"] == 0
    assert match_brief.trials(net, proj, fn)[0]["source"] == NEAR  # what haiku.py's `next` reads


def test_the_next_worker_gets_it_with_its_profile(tmp_path, monkeypatch):
    net, proj, agent = setup(tmp_path, monkeypatch, {("-O1", NEAR): 3})
    net.try_compiler(agent, "game", "f8", TC, "-O1", NEAR)
    other, _ = net.local_agent("b")
    monkeypatch.setattr(net, "_extra_brief", lambda *_: [])  # (no ROM here to say where the function lives)
    fn = net._function("f8", "game")
    for text in (net.get_function("f8", "game", other), claim_state.brief(net, fn, other, "CLAIMED")):
        assert f"Closer attempt under another compiler profile: score 3 with {TC} -O1" in text
        assert NEAR in text and 'suggest(function="f8"' in text
    assert "score 3" in agent_tokens.function_view(net, other, "game", "f8")


def test_it_is_not_shown_once_the_saved_best_is_as_good_or_the_function_changed(tmp_path, monkeypatch):
    net, proj, agent = setup(tmp_path, monkeypatch, {("-O1", NEAR): 3})
    net.try_compiler(agent, "game", "f8", TC, "-O1", NEAR)
    store = net.project_store(proj["id"])
    with store.tx() as db:
        db.execute("UPDATE functions SET best_score=3 WHERE name='f8'")
    assert profile_attempts.brief_lines(net, proj, net._function("f8", "game")) == []
    with store.tx() as db:
        db.execute("UPDATE functions SET best_score=90, size=size+4 WHERE name='f8'")
    assert profile_attempts.kept(net, net._function("f8", "game")) is None


def test_flag_search_and_a_refused_suggestion_keep_it_too(tmp_path, monkeypatch):
    net, proj, agent = setup(tmp_path, monkeypatch, {("-O1", NEAR): 5})
    CompilerMixin._check_compiler(net, proj, TC, ["-O1"], "f8", NEAR)
    fn = net._function("f8", "game")
    assert profile_attempts.kept(net, fn)["score"] == 5
    monkeypatch.setattr(net, "flag_variants", lambda *_: [["-O2", "-G0"], ["-O1"], ["-O3"]])
    monkeypatch.setattr(net, "_judge_with", lambda p, f, tc, flags, source: (
        False, SimpleNamespace(score={"-O2 -G0": 1, "-O1": 5, "-O3": 2}[" ".join(flags)]), ""))
    net.search_flags(agent, "game", "f8", source=NEAR)
    assert profile_attempts.kept(net, fn)["flags"] == ["-O3"]


def test_a_rename_reaches_the_kept_c(tmp_path, monkeypatch):
    net, proj, agent = setup(tmp_path, monkeypatch, {("-O1", NEAR): 3})
    net.try_compiler(agent, "game", "f8", TC, "-O1", NEAR)
    with net.project_store(proj["id"]).tx() as db:
        profile_attempts.rewrite(db, proj["id"], lambda text: text.replace("near", "named"))
    assert "named()" in profile_attempts.kept(net, net._function("f8", "game"))["source"]


def test_haiku_trials_recorded_before_this_are_handed_on_too(tmp_path, monkeypatch):
    from decompnet.app import compiler_experiments
    net, proj, _ = setup(tmp_path, monkeypatch, {})
    fn = net._function("f8", "game")
    compiler_experiments.save_trial(net, fn, "t1", {"agent": 1, "source": NEAR, "compiler": TC, "flags": "-O1",
                                                     "result": {"score": 7, "exact": False}})
    assert profile_attempts.closer(net, proj, fn)["score"] == 7


def test_project_mode_says_how_to_switch_the_file(tmp_path, monkeypatch):
    net, proj, agent = setup(tmp_path, monkeypatch, {})
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=?", (json.dumps({"kind": "fork"}),))
    proj = net.project_row("game")
    fn = net._function("f8", "game")
    profile_attempts.keep(net, proj, fn, "mwcc-2", [], 12, NEAR)
    monkeypatch.setattr(profile_attempts, "_file_compiler", lambda *_: "mwcc-1")
    text = "\n".join(profile_attempts.brief_lines(net, proj, fn))
    assert 'try_compiler(function="f8", compiler="mwcc-2"' in text and "suggest(" not in text
    monkeypatch.setattr(profile_attempts, "_file_compiler", lambda *_: "mwcc-2")
    assert "search_flags(" in "\n".join(profile_attempts.brief_lines(net, proj, fn))
