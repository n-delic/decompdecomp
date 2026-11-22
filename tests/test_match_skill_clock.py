"""The match-function skill's hand-off clock (clock.py): minutes worked and minutes since this agent last improved the
function, read from the app's attempt rows, so "45 minutes without improvement" is measured, not judged."""
import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"


def load():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("clock", SCRIPTS / "clock.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clock = load().clock
START, MIN = 1_000_000.0, 60.0


def row(score, minutes, agent="me"):
    return {"score": score, "t": START + minutes * MIN, "agent": agent}


def test_too_early_to_hand_off_even_with_no_attempts():
    result = clock([row(500, -100, "other")], "me", START, now=START + 30 * MIN)
    assert result["verdict"] == "keep going" and result["start_best"] == 500 and result["wait_min"] == 15.0


def test_an_improvement_restarts_the_clock_and_a_worse_score_does_not():
    rows = [row(500, -100, "other"), row(300, 20), row(400, 50), row(None, 60)]
    result = clock(rows, "me", START, now=START + 70 * MIN)
    assert result["best"] == 300 and result["since_improvement_min"] == 50.0
    assert result["verdict"] == "hand-off allowed" and result["own_attempts"] == 2  # a failed compile has no score
    assert clock(rows + [row(299, 65)], "me", START, now=START + 70 * MIN)["verdict"] == "keep going"


def test_only_beating_everything_before_counts_and_another_agents_low_is_not_ours():
    rows = [row(100, -10, "other"), row(150, 30), row(90, 40, "other"), row(95, 50)]
    result = clock(rows, "me", START, now=START + 60 * MIN)
    assert result["best"] == 90 and result["since_improvement_min"] == 60.0  # 150 and 95 never beat the best then
    assert result["verdict"] == "hand-off allowed"


def test_the_session_file_names_slug_function_agent_and_start(tmp_path, monkeypatch, capsys):
    module = load()
    calls = []

    class App:
        def __init__(self, home):
            calls.append(home)

        def function(self, slug, name):
            calls.append((slug, name))
            return {"scores": [row(10, -5, "x"), row(0, 1, "manual-abc")], "scores_sampled": False}

    monkeypatch.setattr(module.judge, "App", App)
    monkeypatch.setattr(module, "session", lambda holder: ("/home", "game", "f", "manual-abc", START))
    monkeypatch.setattr(module.time, "time", lambda: START + 10 * MIN)
    assert module.main(["claude-x"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert calls == ["/home", ("game", "f")] and out["best"] == 0 and out["since_improvement_min"] == 9.0
