"""fast_judge.py (the match-function skill) needs only a project and a function: the judge worker's `function` op
supplies the target bytes, the function's compiler profile and the project header, and `--diff` shows the app's rows."""
import importlib.util
import json
import stat
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

from decompnet import hosted_judge
from tests.test_hosted import project

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(f"skill_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_worker_describes_a_function_so_a_search_needs_only_its_name(tmp_path):
    net = project(tmp_path)
    info = hosted_judge.function_info(net, {"slug": "game", "name": "f3"})
    assert info["target"] == bytes([3, 0x03, 0xE0, 0x08]).hex() and info["vram"] == 0x80001300
    assert info["header"] == "typedef int s32;" and info["unit"] == "src/a.c"
    assert info["compiler"]["toolchain"] == "gcc-2.7.2-kmc"
    # a function inside a compiler range is judged with that range's profile, as the app does
    assert hosted_judge.function_info(net, {"slug": "game", "name": "f16"})["compiler"] == {"toolchain": "ido-7.1", "flags": ["-O1"]}
    assert "no project" in hosted_judge.function_info(net, {"slug": "nope", "name": "f3"})["error"]
    assert "0 functions" in hosted_judge.function_info(net, {"slug": "game", "name": "missing"})["error"]
    assert "0 functions" in hosted_judge.function_info(net, {"slug": "game", "name": "f3", "vram": 1})["error"]


def test_the_worker_serves_the_function_op_and_returns_the_apps_rows_on_request(tmp_path, monkeypatch):
    net = project(tmp_path)
    rows = SimpleNamespace(score=12, render=lambda: "1 register\n- jr $ra | + jr $v0")
    monkeypatch.setattr(net, "_judge_with", lambda proj, fn, toolchain, flags, source: (False, rows, ""))
    target = bytes([1, 3, 0xE0, 8]).hex()
    request = {"slug": "game", "function": {"name": "f1", "vram": 0x80001100, "target": target},
               "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]}, "source": "int f1(void){return 0;}"}
    assert "diff" not in hosted_judge.judge(net, request)
    reply = hosted_judge.judge(net, request | {"diff": True})
    assert reply["score"] == 12 and "jr $v0" in reply["diff"]
    out = []
    lines = [json.dumps({"op": "function", "slug": "game", "name": "f1"}) + "\n"]
    hosted_judge.serve(net, lines, SimpleNamespace(write=out.append, flush=lambda: None))
    assert json.loads(out[0])["target"] == target


STUB = textwrap.dedent("""\
    #!{python}
    import json, sys
    for line in sys.stdin:
        r = json.loads(line)
        if r["op"] == "function":
            out = {{"name": r["name"], "vram": 4096, "target": "03e00008", "unit": "u", "header": "typedef int s32;",
                   "compiler": {{"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]}}}}
        else:
            assert r["function"]["target"] == "03e00008" and r["compiler"]["flags"] == ["-O2"]
            out = {{"score": 0 if "MATCH" in r["source"] else 30, "detail": "",
                   **({{"diff": "1 row differs\\n- jr $ra | + jr $v0"}} if r.get("diff") else {{}})}}
        print(json.dumps(out), flush=True)
""")


def stub_decomp(tmp_path, monkeypatch):
    path = tmp_path / "decomp"
    path.write_text(STUB.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("DECOMP_BIN", str(path))
    return load("fast_judge")


def test_fast_judge_resolves_the_target_header_and_profile_itself(tmp_path, monkeypatch):
    fast = stub_decomp(tmp_path, monkeypatch)
    judge = fast.Judge("game", "f1")
    try:
        assert judge.header == "typedef int s32;" and judge.compiler["flags"] == ["-O2"]
        assert judge.score("int f1(void){MATCH}") == 0 and judge.score("other") == 30
        assert "jr $v0" in judge.judge("other", diff=True)["diff"]
    finally:
        judge.close()


def test_fast_judge_prints_the_score_and_then_the_differing_rows(tmp_path, monkeypatch, capsys):
    fast = stub_decomp(tmp_path, monkeypatch)
    source = tmp_path / "c.c"
    source.write_text("int f1(void){return 1;}")
    fast.main([str(source), "--slug", "game", "--function", "f1", "--diff"])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "30" and "1 row differs" in out[1]
    fast.main([str(source), "--slug", "game", "--function", "f1", "--header"])
    assert capsys.readouterr().out.strip() == "typedef int s32;"
