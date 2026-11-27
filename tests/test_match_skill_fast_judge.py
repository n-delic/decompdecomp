"""fast_judge.py (the match-function skill) needs only a project and a function: the judge worker's `function` op
supplies the target bytes, the function's compiler profile and the project header, and `--diff` shows the app's rows."""
import importlib.util
import json
import stat
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import hosted_judge, scoring
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
    # the context the app's draft path gives draft_repairs.variants travels with it (prove_chain.py passes it on)
    assert {"tables", "callees", "entry_scope", "words"} <= set(info) and isinstance(info["tables"], list)
    assert info["words"] == {}  # (no float constant loaded through $at: float_literal_words)
    assert isinstance(info["big_endian"], bool)
    assert "no project" in hosted_judge.function_info(net, {"slug": "nope", "name": "f3"})["error"]
    assert "0 functions" in hosted_judge.function_info(net, {"slug": "game", "name": "missing"})["error"]
    assert "0 functions" in hosted_judge.function_info(net, {"slug": "game", "name": "f3", "vram": 1})["error"]


def test_the_worker_serves_the_function_op_and_returns_the_apps_rows_on_request(tmp_path, monkeypatch):
    net = project(tmp_path)
    rows = SimpleNamespace(score=12, render=lambda **kw: "1 register\n- jr $ra | + jr $v0")
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


def test_the_worker_shows_sixty_rows_by_default_and_all_of_them_on_request(tmp_path, monkeypatch):
    net = project(tmp_path)
    rows = [("|", f"addu $t0, $t0, $t{i % 8}", f"addu $t1, $t1, $t{i % 8}") for i in range(200)]
    diff = SimpleNamespace(score=5, render=lambda **kw: scoring.render_rows(rows, **kw))
    monkeypatch.setattr(net, "_judge_with", lambda proj, fn, toolchain, flags, source: (False, diff, ""))
    target = bytes([1, 3, 0xE0, 8]).hex()
    request = {"slug": "game", "function": {"name": "f1", "vram": 0x80001100, "target": target}, "diff": True,
               "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]}, "source": "int f1(void){return 0;}"}
    short = hosted_judge.judge(net, request)["diff"]
    assert len(short.splitlines()) < 70 and "more rows not shown" in short and "last_diff" not in short
    assert len(hosted_judge.judge(net, request | {"rows": 10})["diff"].splitlines()) < 20
    diff.rows = rows  # every compiled reply carries a digest of the candidate's code (invariant.py's hint)
    code = hosted_judge.judge(net, request)["code"]
    assert len(code) == 16 and code == hosted_judge.judge(net, request | {"diff": False})["code"]
    full = hosted_judge.judge(net, request | {"rows": 10**9})["diff"]
    assert "more rows not shown" not in full and len(full.splitlines()) > 200


STUB_ROWS = textwrap.dedent("""\
    #!{python}
    import json, sys
    for line in sys.stdin:
        r = json.loads(line)
        if r["op"] == "function":
            out = {{"name": r["name"], "vram": 4096, "target": "03e00008", "header": "",
                   "compiler": {{"toolchain": "t", "flags": []}}}}
        else:
            out = {{"score": 30, "detail": "", "diff": "rows=%s" % r.get("rows")}}
        print(json.dumps(out), flush=True)
""")


def test_fast_judge_diff_rows_default_stays_unset_and_all_lifts_the_cap(tmp_path, monkeypatch, capsys):
    path = tmp_path / "decomp"
    path.write_text(STUB_ROWS.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("DECOMP_BIN", str(path))
    fast = load("fast_judge")
    source = tmp_path / "c.c"
    source.write_text("int f1(void){return 1;}")
    base = [str(source), "--slug", "game", "--function", "f1", "--diff"]
    fast.main(base)
    assert "rows=60" in capsys.readouterr().out
    fast.main(base + ["--rows", "7"])
    assert "rows=7" in capsys.readouterr().out
    fast.main(base + ["--rows", "all"])
    assert f"rows={fast.ALL_ROWS}" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        fast.main(base + ["--rows", "lots"])


def test_fast_judge_prints_the_score_and_then_the_differing_rows(tmp_path, monkeypatch, capsys):
    fast = stub_decomp(tmp_path, monkeypatch)
    source = tmp_path / "c.c"
    source.write_text("int f1(void){return 1;}")
    fast.main([str(source), "--slug", "game", "--function", "f1", "--diff"])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "30" and "1 row differs" in out[1]
    fast.main([str(source), "--slug", "game", "--function", "f1", "--header"])
    assert capsys.readouterr().out.strip() == "typedef int s32;"


def test_prove_chain_offers_the_diff_gated_children_the_permuter_would(monkeypatch):
    """draft_repairs.variants never sees the judge's rows, so a family that reads them (boolean_guard_operands' sink)
    was invisible to a chain even though the Deep Permuter offers it."""
    from decompnet.app import diff_gated_repairs, draft_repairs
    prove = load("prove_chain")
    seen = {}
    monkeypatch.setattr(draft_repairs, "variants", lambda source, *a, **k: [source + "// plain"])
    monkeypatch.setattr(diff_gated_repairs, "proposals", lambda *a, **k: seen.update(args=a, kw=k) or [
        ("gated", a[0] + "// gated"), ("dup", a[0] + "// plain")])
    judge = SimpleNamespace(arch="mips", asm="asm", header="h", compiler={"toolchain": "gcc-2.7.2-kmc", "flags": ["-O2"]},
                            big_endian=False, rows=lambda source: (5, [("r", "a", "b")]))
    out = prove.generator(judge, "f")("void f(void){}")
    # (a gated family writing the same source as another keeps its name too, so --families may ask for it)
    assert [family for family, _ in out] == ["unlabelled", "gated", "dup"]
    assert out[0][1] == out[2][1]
    assert seen["args"][5] == [("r", "a", "b")] and seen["kw"]["big_endian"] is False


STUB_FLAGS = textwrap.dedent("""\
    #!{python}
    import json, sys
    for line in sys.stdin:
        r = json.loads(line)
        if r["op"] == "function":
            out = {{"name": r["name"], "vram": 4096, "target": "03e00008", "header": "",
                   "compiler": {{"toolchain": "t", "flags": ["-O2"]}}}}
        else:
            out = {{"score": 30, "detail": "", "diff": "flags=%s" % " ".join(r["compiler"]["flags"])}}
        print(json.dumps(out), flush=True)
""")


def test_fast_judge_flags_adds_to_the_functions_own_without_replacing_them(tmp_path, monkeypatch, capsys):
    path = tmp_path / "decomp"
    path.write_text(STUB_FLAGS.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("DECOMP_BIN", str(path))
    fast = load("fast_judge")
    source = tmp_path / "c.c"
    source.write_text("int f1(void){return 1;}")
    base = [str(source), "--slug", "game", "--function", "f1", "--diff"]
    fast.main(base)
    assert "flags=-O2\n" in capsys.readouterr().out
    fast.main(base + ["--flags", "-fno-strength-reduce -fno-schedule-insns"])
    assert "flags=-O2 -fno-strength-reduce -fno-schedule-insns" in capsys.readouterr().out


STUB_PROFILE = STUB_FLAGS.replace('"flags=%s" % " ".join(r["compiler"]["flags"])',
                                  '"used=%s %s" % (r["compiler"]["toolchain"], " ".join(r["compiler"]["flags"]))')


def test_fast_judge_names_the_compiler_replaces_flags_and_says_what_it_judged_with(tmp_path, monkeypatch, capsys):
    path = tmp_path / "decomp"
    path.write_text(STUB_PROFILE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("DECOMP_BIN", str(path))
    monkeypatch.delenv("DD_EXTRA_FLAGS", raising=False)
    fast = load("fast_judge")
    source = tmp_path / "c.c"
    source.write_text("int f1(void){return 1;}")
    base = [str(source), "--slug", "game", "--function", "f1", "--diff"]
    fast.main(base + ["--toolchain", "ido-7.1", "--replace-flags", "-O1 -mips2"])
    out = capsys.readouterr()
    assert "used=ido-7.1 -O1 -mips2\n" in out.out and out.out.splitlines()[0] == "30"
    assert "judged with ido-7.1 -O1 -mips2" in out.err and "--replace-flags" in out.err
    monkeypatch.setenv("DD_EXTRA_FLAGS", "-mfp32")  # the environment's flags count when --flags isn't given
    fast.main(base)
    out = capsys.readouterr()
    assert "used=t -O2 -mfp32\n" in out.out and "added: -mfp32" in out.err
    fast.main(base + ["--replace-flags=-O1"])
    assert "used=t -O1\n" in capsys.readouterr().out


def test_a_copy_of_the_scripts_finds_the_decomp_of_the_venv_running_it(tmp_path, monkeypatch):
    fast = load("fast_judge")
    for name in ("DECOMP_BIN", "UV_PROJECT_ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "decomp").write_text("")
    monkeypatch.setattr(fast.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(fast.sys, "base_prefix", "/usr")
    assert fast.find_decomp() == str(tmp_path / "venv" / "bin" / "decomp")


def test_a_flag_line_given_to_judge_as_a_string_is_not_split_into_characters(tmp_path, monkeypatch):
    fast = stub_decomp(tmp_path, monkeypatch)
    judge = fast.Judge("game", "f1", flags="-G0 -O1", extra_flags="-mfp32")
    try:
        assert judge.compiler["flags"] == ["-G0", "-O1", "-mfp32"]
    finally:
        judge.close()
