"""Local compiler comparisons report better matches without changing project settings."""

import json
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork


def test_find_better_settings_is_local_and_report_only(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    source = "int f(int x) { return x + 1; }"
    cfg = {"kind": "rom", "platform": "ps", "compiler": {"toolchain": "gcc-2.7.2-psx", "flags": ["-O2", "-G8"]}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,n_insns,status,best_score,best_source) "
                   "VALUES(?,?,?,?,?,?,?)", (pid, "f", 0x80010000, 16, "open", 160, source))
        db.execute("INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) "
                   "VALUES(?,?,?,?,?,?,?)", (pid, 1, "int shared_value;", None, "test", "2026-01-01", ""))
    monkeypatch.setattr(toolchains, "installed", lambda tc: {"installed": True} if tc == "gcc-2.7.2-psx" else None)
    monkeypatch.setattr(toolchains, "get", lambda tc: SimpleNamespace(supported=True, name=tc, flags=["-O2", "-G8"]))
    monkeypatch.setattr(toolchains, "runtime_error", lambda tc: None)
    monkeypatch.setattr(net, "flag_variants", lambda tc, flags: [flags, ["-O3", "-G8"]])
    judged = []

    def judge(proj, fn, tc, flags, candidate):
        judged.append((tc, flags, candidate))
        return flags[0] == "-O3", SimpleNamespace(percent=75.0), ""

    monkeypatch.setattr(net, "_judge_with", judge)
    result = net.find_better_compiler_settings("demo", log=lambda *_: None)
    assert result["tried"] == 1
    assert result["results"][0]["best"] == 100.0
    assert result["results"][0]["profile"] == ["gcc-2.7.2-psx", ["-O3", "-G8"]]
    assert all(tc == "gcc-2.7.2-psx" and candidate == source for tc, _, candidate in judged)
    assert json.loads(net._project("demo")["config"]) == cfg
    assert net.compiler_comparison_results("demo")[0]["stale"] is False
    assert net.find_better_compiler_settings("demo", log=lambda *_: None)["skipped"] == 1
    assert len(judged) == 3  # unchanged inputs do not repeat the compiles
    assert net.find_better_compiler_settings("demo", log=lambda *_: None, untested_only=False)["tried"] == 1
    applied, phases = [], []
    monkeypatch.setattr(net, "apply_compiler_test_result", lambda *args: applied.append(args))

    def report(_line):
        pass

    report.progress = lambda value: phases.append(value["phase"])
    again = net.find_better_compiler_settings("demo", log=report, untested_only=False, auto_apply=True)
    assert again["applied_count"] == 1 and applied[0][1] == "f"
    assert "testing" in phases and "applying" in phases


def test_comparison_options_and_changed_inputs(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    cfg = {"kind": "rom", "platform": "ps", "compiler": {"toolchain": "gcc-2.7.2-psx", "flags": ["-O2", "-G8"]}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,n_insns,status,best_source) VALUES(?,?,?,?,?,?)",
                   (pid, "f", 0x80010000, 16, "open", "int f(void) { return 1; }"))
    monkeypatch.setattr(toolchains, "installed", lambda tc: {"installed": True} if tc == "gcc-2.7.2-psx" else None)
    monkeypatch.setattr(toolchains, "get", lambda tc: SimpleNamespace(supported=True, name=tc, flags=["-O2", "-G8"]))
    monkeypatch.setattr(toolchains, "runtime_error", lambda tc: None)
    monkeypatch.setattr(net, "flag_variants", lambda tc, flags: [flags, ["-O3", "-G8"]])
    checked = []

    def judge(proj, fn, tc, flags, source):
        checked.append(flags)
        return False, SimpleNamespace(percent=25.0), ""

    monkeypatch.setattr(net, "_judge_with", judge)
    assert net.find_better_compiler_settings("demo", log=lambda *_: None, flags_only=True)["tried"] == 1
    assert checked and all(flags[0] in ("-O2", "-O3") for flags in checked)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source=? WHERE project_id=?", ("int f(void) { return 2; }", pid))
    assert net.compiler_comparison_results("demo")[0]["stale"] is True
    assert net.find_better_compiler_settings("demo", log=lambda *_: None, flags_only=True)["tried"] == 1


def test_saved_result_is_rechecked_before_applying_one_function(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    cfg = {"kind": "rom", "platform": "ps", "compiler": {"toolchain": "gcc-2.7.2-psx", "flags": ["-O2", "-G8"]}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        fid = db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,best_score,best_source) "
                         "VALUES(?,?,?,?,?,?,?,?)", (pid, "f", 0x80010000, 64, 16, "open", 160,
                                                     "int f(void) { return 1; }")).lastrowid
    monkeypatch.setattr(toolchains, "installed", lambda tc: {"installed": True} if tc == "gcc-2.7.2-psx" else None)
    monkeypatch.setattr(toolchains, "get", lambda tc: SimpleNamespace(supported=True, name=tc, flags=["-O2", "-G8"]))
    monkeypatch.setattr(toolchains, "runtime_error", lambda tc: None)
    monkeypatch.setattr(net, "flag_variants", lambda tc, flags: [flags, ["-O3", "-G8"]])
    checked = []

    def judge(proj, fn, tc, flags, source):
        checked.append(flags)
        return flags[0] == "-O3", SimpleNamespace(percent=75.0, score=40), ""

    monkeypatch.setattr(net, "_judge_with", judge)
    net.find_better_compiler_settings("demo", log=lambda *_: None)
    changed = []
    monkeypatch.setattr(net, "set_compiler_ranges", lambda *args: changed.append(args))
    monkeypatch.setattr(net, "try_quietly", lambda *args: None)
    assert net.apply_compiler_test_result("demo", "f", "gcc-2.7.2-psx", ["-O3", "-G8"])["matched"] is False
    assert changed[0][1] == [[0x80010000, 0x80010040]]
    assert len(checked) >= 4  # application judged the selected profile again
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source=? WHERE id=?", ("int f(void) { return 2; }", fid))
    with pytest.raises(Exception, match="inputs changed"):
        net.apply_compiler_test_result("demo", "f", "gcc-2.7.2-psx", ["-O3", "-G8"])
