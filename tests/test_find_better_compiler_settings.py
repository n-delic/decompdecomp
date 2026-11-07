"""Local compiler comparisons report better matches without changing project settings."""

import json
from types import SimpleNamespace

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
