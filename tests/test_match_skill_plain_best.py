"""plain_best.py and setup.py's orientation: a claim starts with the saved best, its scaffolding-stripped form and its
clean rewrites."""
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
    monkeypatch.setattr(plain_best, "clean_forms", lambda source, name, header="": [])
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


def test_the_clean_rewrites_are_scored_and_written_after_the_plain_form(tmp_path, monkeypatch):
    plain_best = load("plain_best")
    monkeypatch.setattr(plain_best, "plain_form", lambda source, name, header="": None)
    monkeypatch.setattr(plain_best, "clean_forms", lambda source, name, header="": [
        ("clean_rewrite_structure", "void f(int x) { if (x) g = 1; }"), ("clean_rewrite", "void f(int x) { g = 1; }")])
    lines = []
    found = plain_best.report(FENCED, "f", lambda text: 40 if "while" in text else (None if "if" in text else 70),
                              out_dir=tmp_path, write=lines.append)
    assert found == {"best": 40, "clean_rewrite_structure": None, "clean_rewrite": 70}
    assert (tmp_path / "clean_rewrite.c").read_text() == "void f(int x) { g = 1; }"
    assert "did not compile" in lines[2] and lines[3].startswith("clean_rewrite ") and "score 70" in lines[3]


def test_the_library_clean_rewrite_structures_an_m2c_loop():
    plain_best = load("plain_best")
    try:
        from decomprepair import clean_rewrite  # noqa: F401
    except ImportError:
        assert plain_best.clean_forms(FENCED, "f") == []  # an older pin: no clean rewrite, no error
        return
    source = "int g;\nvoid f(int x) {\nloop_1:\n    g += 1;\n    if (g < x) goto loop_1;\n}\n"
    levels = dict(plain_best.clean_forms(source, "f"))
    assert "goto" not in levels["clean_rewrite_structure"] and "while (g < x)" in levels["clean_rewrite_structure"]


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


def test_a_much_closer_optimisation_level_is_told_and_the_compiler_is_put_back():
    plain_best = load("plain_best")
    compiler = {"toolchain": "gcc-2.7.2-kmc", "flags": ["-G0", "-mips3", "-O2"]}
    lines, seen = [], []

    def score(source):
        seen.append(compiler["flags"])
        return 40 if "-O1" in compiler["flags"] else 90

    found = plain_best.level_report("C", compiler, 100, score, write=lines.append)
    assert found["-G0 -mips3 -O1"] == 40 and compiler["flags"] == ["-G0", "-mips3", "-O2"]
    assert not any("-O2" in flags for flags in seen)  # its own level is already scored
    assert lines[0].startswith("levels ") and "-O1 40" in lines[0] and "closer under -G0 -mips3 -O1" in lines[1]
    lines.clear()
    plain_best.level_report("C", compiler, 45, score, write=lines.append)  # 40 against 45 is not far closer
    assert len(lines) == 1
