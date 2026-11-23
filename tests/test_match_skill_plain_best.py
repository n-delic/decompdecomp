"""plain_best.py and setup.py's orientation: a claim starts with the saved best and its scaffolding-stripped form."""
import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"


def load(name):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FENCED = "int g;\nvoid f(int x) {\n    do { if (x) { g = 1; } else { g = 1; } } while (0);\n}\n"


def test_the_best_and_its_plain_form_are_scored_and_written(tmp_path, monkeypatch):
    plain_best = load("plain_best")
    monkeypatch.setattr(plain_best, "plain_form", lambda source, name, header="": "void f(int x) { g = 1; }")
    lines = []
    found = plain_best.report(FENCED, "f", lambda text: 40 if "while" in text else 90, out_dir=tmp_path,
                              write=lines.append)
    assert found == {"best": 40, "plain": 90}
    assert (tmp_path / "best.c").read_text() == FENCED and "g = 1" in (tmp_path / "plain.c").read_text()
    assert lines[0].startswith("saved best   score 40") and lines[1].startswith("plain form   score 90")
    monkeypatch.setattr(plain_best, "plain_form", lambda source, name, header="": None)
    lines.clear()
    assert plain_best.report(FENCED, "f", lambda text: 40, write=lines.append) == {"best": 40}
    assert "no scaffolding" in lines[1]


def test_the_library_plain_form_strips_fences_and_identical_arms():
    plain_best = load("plain_best")
    from decomprepair import scaffold_strip
    if not hasattr(scaffold_strip, "plain"):
        assert plain_best.plain_form(FENCED, "f") is None  # an older pin: no plain form, no error
        return
    text = plain_best.plain_form(FENCED, "f")
    assert "while (0)" not in text and "else" not in text


def test_setup_runs_the_orientation_scripts_with_the_session_python_and_survives_a_timeout(tmp_path):
    setup = load("setup")
    calls, lines = [], []

    def run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        if len(calls) > 1:
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 0, "saved best   score 40\n", "")
    info = {"slug": "s", "function": "f", "python": "/venv/bin/python", "venv": "/venv", "home": "/home"}
    setup.orient(info, tmp_path, run=run, write=lines.append)
    command, env = calls[0]
    assert command[0] == "/venv/bin/python" and command[1].endswith("plain_best.py")
    assert command[2:] == ["--slug", "s", "--function", "f", "--out-dir", str(tmp_path / "start")]
    assert env["UV_PROJECT_ENVIRONMENT"] == "/venv" and env["DECOMP_HOME"] == "/home"
    assert "saved best   score 40" in lines[0]
    setup.ORIENT_SCRIPTS = setup.ORIENT_SCRIPTS * 2
    lines.clear()
    setup.orient(info, tmp_path, run=run, write=lines.append)
    assert "skipped" in lines[-1]
