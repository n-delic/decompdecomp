"""Local compiler comparisons report better matches without changing project settings."""

import json
from types import SimpleNamespace

import pytest

from decompnet import consoles, toolchains
from decompnet.app.engine import RomNetwork


def test_own_compiler_is_usable_in_profile_selection(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'ps',
           'compiler': {'toolchain': 'gcc-2.7.2-psx', 'flags': ['-O2']}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('demo', 'Demo', str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute('INSERT INTO functions(project_id,name,vram,n_insns,status) VALUES(?,?,?,?,?)',
                   (pid, 'f', 0x80010000, 1, 'open'))
    monkeypatch.setattr(toolchains, 'installed', lambda tc: {'own': True} if tc == 'gcc-2.7.2-psx' else None)
    monkeypatch.setattr(toolchains, 'get', lambda tc: SimpleNamespace(supported=False))
    monkeypatch.setattr(toolchains, 'runtime_error', lambda tc: None)
    monkeypatch.setattr(consoles, 'default_flags', lambda cfg, tc: ['-O2'])
    monkeypatch.setattr(consoles, 'of', lambda cfg: SimpleNamespace(
        compilers=[SimpleNamespace(id='gcc-2.7.2-psx')]))
    assert ('gcc-2.7.2-psx', ('-O2',)) in net._compiler_comparison_profiles(
        net._project('demo'), used_profiles_only=False)
    assert net._compiler_comparison_profiles(net._project('demo'), used_profiles_only=True) == [
        ('gcc-2.7.2-psx', ('-O2',))]


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
    monkeypatch.setattr(net, "flag_variants", lambda tc, flags, *args: [flags, ["-O3", "-G8"]])
    judged = []

    def judge(proj, fn, tc, flags, candidate):
        judged.append((tc, flags, candidate))
        return flags[0] == "-O3", SimpleNamespace(percent=75.0), ""

    monkeypatch.setattr(net, "_judge_with", judge)
    result = net.find_better_compiler_settings("demo", log=lambda *_: None)
    assert result["tried"] == 1
    saved = net.compiler_comparison_results("demo")[0]  # the job's result has counts; the drawer reads these
    assert saved["best"] == 100.0
    assert saved["profile"] == ["gcc-2.7.2-psx", ["-O3", "-G8"]]
    assert saved["before_profile"] == ["gcc-2.7.2-psx", ["-O2", "-G8"]]
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

    report.progress = phases.append
    again = net.find_better_compiler_settings("demo", log=report, untested_only=False, auto_apply=True)
    assert again["applied_count"] == 1 and applied[0][1] == "f"
    assert {"testing", "applying"} <= {p["phase"] for p in phases}
    # #241: /api/job sends the progress every 500 ms; every result so far was megabytes on a large game
    assert not any("results" in p or "ranked" in p for p in phases) and "results" not in again
    applied.clear()
    monkeypatch.setattr(net, '_judge_with', lambda *args: (False, SimpleNamespace(percent=95.0), ''))
    partial = net.find_better_compiler_settings('demo', log=lambda *_: None,
                                                untested_only=False, auto_apply=True)
    assert partial['applied_count'] == 0 and not applied


def test_comparison_options_and_changed_inputs(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    cfg = {"kind": "rom", "platform": "ps", "compiler": {"toolchain": "gcc-2.7.2-psx", "flags": ["-O2", "-G8"]},
           "compiler_ranges": [{"start": 0x80010000, "end": 0x80010040,
                                "toolchain": "gcc-2.7.2-psx", "flags": ["-O1", "-G8"]}]}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,n_insns,status,best_source) VALUES(?,?,?,?,?,?)",
                   (pid, "f", 0x80010000, 16, "open", "int f(void) { return 1; }"))
    monkeypatch.setattr(toolchains, "installed", lambda tc: {"installed": True} if tc == "gcc-2.7.2-psx" else None)
    monkeypatch.setattr(toolchains, "get", lambda tc: SimpleNamespace(supported=True, name=tc, flags=["-O2", "-G8"]))
    monkeypatch.setattr(toolchains, "runtime_error", lambda tc: None)
    monkeypatch.setattr(net, "flag_variants", lambda tc, flags, *args: [flags, ["-O3", *flags[1:]]])
    checked = []

    def judge(proj, fn, tc, flags, source):
        checked.append(flags)
        return False, SimpleNamespace(percent=25.0), ""

    monkeypatch.setattr(net, "_judge_with", judge)
    assert net.find_better_compiler_settings("demo", log=lambda *_: None, flags_only=True)["tried"] == 1
    assert checked and all(flags[0] in ("-O1", "-O3") for flags in checked)
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
    monkeypatch.setattr(net, "flag_variants", lambda tc, flags, *args: [flags, ["-O3", "-G8"]])
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


def test_saved_comparisons_go_with_their_project(tmp_path):
    """#241: function ids are reused, so a comparison left behind by a removed project showed in the next one."""
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    cfg = {"kind": "rom", "platform": "ps", "compiler": {"toolchain": "gcc-2.7.2-psx", "flags": ["-O2"]}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        fid = db.execute("INSERT INTO functions(project_id,name,vram,n_insns,status) VALUES(?,?,?,?,?)",
                         (pid, "f", 0x80010000, 1, "open")).lastrowid
        db.execute("INSERT INTO compiler_tests VALUES(?,?,?,?)", (fid, "digest", 0.0, "{}"))
    net.remove_project("demo")
    assert net.store.one("SELECT 1 FROM compiler_tests WHERE function_id=?", fid) is None
