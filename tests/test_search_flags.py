"""Flags searched, not only compilers (search_flags): the C that looks right but won't match is often built with the
wrong flags. Each variant is judged like an attempt and recorded nowhere."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork

from .test_app_engine import CLAMP
from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def test_the_variants_change_one_flag_each():
    net = RomNetwork(":memory:", ".")
    base = ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]
    variants = net.flag_variants("gcc-2.7.2-kmc", base)
    assert variants[0] == base and ["-G0", "-mips3", "-mgp32", "-mfp32", "-O1"] in variants
    assert ["-G8", "-mips3", "-mgp32", "-mfp32", "-O2"] in variants and [*base, "-fno-strength-reduce"] in variants
    assert all(sum(a != b for a, b in zip(v, base)) <= 1 for v in variants if len(v) == len(base))
    cw = net.flag_variants("mwcps2-3.0.1", ["-O3", "-sdatathreshold", "8"])
    assert ["-O3", "-sdatathreshold", "0"] in cw and ["-O4", "-sdatathreshold", "8"] in cw
    snc = net.flag_variants("pspsnc-1.2.7503", ["-O2", "-G8"])  # SNC has no -Os (it refuses to build with it)
    assert ["-O4", "-G8"] in snc and ["-O2", "-G0"] in snc and ["-O2", "-G8", "-Xsched=0"] in snc
    assert not any("-Os" in v or "-fno-strength-reduce" in v for v in snc)


def test_a_project_with_the_wrong_flags_hears_which_ones_match(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    net._update_cfg(slug, lambda cfg: cfg["compiler"].__setitem__(
        "flags", [f if f != "-O2" else "-O1" for f in cfg["compiler"]["flags"]]))  # the project's flags are wrong
    said = net.search_flags(agent, slug, "func_800007D0", source=CLAMP)
    assert "MATCHES with" in said and "-O2" in said.split("MATCHES with")[1].split(".")[0], said
    assert json.loads(net._project(slug)["config"])["compiler"]["flags"][-1] == "-O1"  # it only says, changes nothing
    with pytest.raises(Exception, match="no attempt yet"):
        net.search_flags(agent, slug, "func_800007D0")


@pytest.mark.parametrize("toolchain", ["gcc-2.7.2-kmc", "ee-gcc-2.95.3"])
@pytest.mark.parametrize("debug", [None, "-g", "-g2", "-g3"])
def test_gcc_debug_search_replaces_the_debug_level(toolchain, debug):
    net = RomNetwork(":memory:", ".")
    plain = ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]
    base = plain + ([debug] if debug else [])
    variants = net.flag_variants(toolchain, base)
    assert variants[0] == base
    for flags in (plain, [*plain, "-g"], [*plain, "-g3"]):
        assert flags in variants
    assert all(sum(f.startswith("-g") for f in flags) <= 1 for flags in variants)
    assert len(variants) <= 12


@pytest.mark.parametrize("debug", ["-g", "-g3"])
def test_the_survey_retries_old_misses_with_debug_flags(tmp_path, monkeypatch, debug):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    source = "int f(int x) { return x + 1; }"
    flags = ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]
    cfg = {"kind": "rom", "platform": "n64", "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": flags},
           "compiler_survey": {"tried": {str(0x80001000): hashlib.sha1(source.encode()).hexdigest()[:16]}}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,n_insns,status,best_score,best_source) "
                   "VALUES(?,?,?,?,?,?,?)", (pid, "f", 0x80001000, 16, "open", 160, source))
    monkeypatch.setattr(net, "compiler_map", lambda *a, **k: SimpleNamespace(contradicted=set()))
    monkeypatch.setattr(net, "_suggest_from_map", lambda *a: [])
    judged = []

    def judge(proj, fn, tc, variant, candidate):
        judged.append(variant)
        return debug in variant, None, ""

    monkeypatch.setattr(net, "_judge_with", judge)
    result = net.survey_compilers("demo")
    assert result["confirmed"] == 1
    saved = json.loads(net._project("demo")["config"])
    assert saved["compiler_survey"]["confirmed"][str(0x80001000)] == ["gcc-2.7.2-kmc", [*flags, debug]]
    assert saved["compiler"] == cfg["compiler"]  # detection alone does not change the project's flags
    before = len(judged)
    assert net.survey_compilers("demo")["tried"] == 0
    assert len(judged) == before


@pytest.mark.parametrize("function", ["", "f"])
def test_installed_compiler_comparison_is_local_and_report_only(tmp_path, monkeypatch, function):
    from decompnet import toolchains

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    source = "int f(int x) { return x + 1; }"
    cfg = {"kind": "rom", "platform": "ps", "compiler": {"toolchain": "gcc-2.7.2-psx", "flags": ["-O2", "-G8"]},
           "auto_compiler": True}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,n_insns,status,best_score,best_source) "
                   "VALUES(?,?,?,?,?,?,?)", (pid, "f", 0x80010000, 16, "open", 160, source))
    monkeypatch.setattr(net, "compiler_map", lambda *a, **k: SimpleNamespace(contradicted=set()))
    monkeypatch.setattr(toolchains, "installed", lambda tc: {"installed": True} if tc in (
        "gcc-2.7.2-psx", "gcc-2.8.0-psx") else None)
    monkeypatch.setattr(toolchains, "get", lambda tc: SimpleNamespace(supported=True, flags=["-O2", "-G8"]))
    monkeypatch.setattr(toolchains, "runtime_error", lambda tc: None)
    judged = []

    def judge(proj, fn, tc, flags, candidate):
        judged.append(tc)
        return True, None, ""

    monkeypatch.setattr(net, "_judge_with", judge)
    monkeypatch.setattr(net, "_suggest_from_map", lambda *a: pytest.fail("comparison must not apply suggestions"))
    snapshots = []
    def log(line):
        pass
    log.progress = snapshots.append
    result = net.survey_compilers("demo", all_installed=True, function=function, log=log)
    snapshots = [row for row in snapshots if "tested" in row]
    assert snapshots[0]["tested"] == 0 and snapshots[0]["completed"] == 0
    assert snapshots[1]["tested"] == 1 and snapshots[1]["best"] == 100
    assert snapshots[0]["exact_matches_current"] == 0
    assert snapshots[1]["exact_matches_current"] == 1
    assert snapshots[-1]["exact_matches_current"] == len(judged)
    assert snapshots[-1]["tested"] == snapshots[-1]["combinations"]
    assert snapshots[-1]["completed"] == snapshots[-1]["improved"] == snapshots[-1]["exact"] == 1
    assert snapshots[0]["results"] == []
    assert snapshots[-1]["results"] == result["results"]
    assert result["results"][0]["function"] == "f"
    assert result["results"][0]["improved"] and result["results"][0]["exact"]
    assert result["confirmed"] == 1
    assert result["improvements"][0]["function"] == "f"
    assert result["improvements"][0]["best"] == 100
    matches = result["matches"][str(0x80010000)]
    assert {m[0] for m in matches} == {"gcc-2.7.2-psx", "gcc-2.8.0-psx"}
    assert len(matches) > (6 if function else 1)
    assert len(matches) == len(judged)  # no early exit, including when current settings match
    assert set(judged) <= {"gcc-2.7.2-psx", "gcc-2.8.0-psx"}
    assert json.loads(net._project("demo")["config"]) == cfg
    if not function:
        profiles = []

        def flags_judge(proj, fn, tc, flags, candidate):
            profiles.append((tc, tuple(flags)))
            return "-O3" in flags and "-g3" in flags, None, ""

        monkeypatch.setattr(net, "_judge_with", flags_judge)
        expanded = net.survey_compilers("demo", all_installed=True, log=lambda *_: None)
        project_profiles = set(profiles)
        assert expanded["improvements"][0]["best"] == 100
        assert all("-O3" in m[1] and "-g3" in m[1] for m in expanded["matches"][str(0x80010000)])
        profiles.clear()
        net.survey_compilers("demo", all_installed=True, function="f", log=lambda *_: None)
        assert set(profiles) == project_profiles
    if function:
        assert result["comparison"]["improved"] is True
        assert result["comparison"]["best"] == 100
        monkeypatch.setattr(net, "_judge_with", lambda *a: (False, SimpleNamespace(percent=80.0), ""))
        messages = []
        missed = net.survey_compilers("demo", all_installed=True, function=function, log=messages.append)
        assert missed["comparison"]["improved"] is False
        assert missed["comparison"]["best"] == 80.0
        assert missed["improvements"] == []
        assert all(r["percent"] == 80.0 for r in missed["comparison"]["ranked"])
        assert not any("400" in line for line in messages)

        score_lines = [line for line in messages if " · " in line and line.split(" · ")[0].split("/")[0].isdigit()]
        assert len(score_lines) == missed["comparison"]["tested"]
        assert all("80.00%" in line for line in score_lines)
        monkeypatch.setattr(net, "_judge_with", lambda *a: (False, None, "compile error"))
        messages.clear()
        failed = net.survey_compilers("demo", all_installed=True, function=function, log=messages.append)
        assert failed["comparison"]["best"] is None
        assert all(r["percent"] is None for r in failed["comparison"]["ranked"])
        scores = iter([70.0, 95.0, 80.0] * 1000)
        monkeypatch.setattr(net, "_judge_with", lambda *a: (False, SimpleNamespace(percent=next(scores)), ""))
        ranked = net.survey_compilers("demo", all_installed=True, function=function, log=lambda *_: None)["comparison"]["ranked"]
        assert [r["percent"] for r in ranked] == sorted((r["percent"] for r in ranked), reverse=True)
        assert any("could not compile or compare" in line for line in messages)


def test_apply_function_compiler_preserves_neighbours_and_revalidates(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    cfg = {"kind": "rom", "platform": "ps", "compiler": {"toolchain": "gcc-2.7.2-psx", "flags": ["-O2"]},
           "compiler_ranges": [{"start": 0x1000, "end": 0x1100, "toolchain": "gcc-2.7.2-psx", "flags": ["-O1"]}]}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,best_score,best_source) "
                   "VALUES(?,?,?,?,?,?,?,?)", (pid, "f", 0x1040, 32, 8, "open", 200, "int f(void){return 1;}"))
    monkeypatch.setattr(net, "_judge_with", lambda *a: (False, SimpleNamespace(score=100), ""))
    attempts = []
    monkeypatch.setattr(net, "try_quietly", lambda *a, **k: attempts.append((a, k)))
    net.apply_function_compiler("demo", "f", "gcc-2.8.0-psx", ["-O2"])
    ranges = net.cfg(net._project("demo"))["compiler_ranges"]
    assert [(r["start"], r["end"]) for r in ranges] == [(0x1000, 0x1040), (0x1040, 0x1060), (0x1060, 0x1100)]
    assert ranges[0]["flags"] == ranges[2]["flags"] == ["-O1"]
    assert ranges[1]["toolchain"] == "gcc-2.8.0-psx" and len(attempts) == 1
    before = net.cfg(net._project("demo"))
    monkeypatch.setattr(net, "_judge_with", lambda *a: (False, SimpleNamespace(score=300), ""))
    with pytest.raises(Exception, match="no longer improve"):
        net.apply_function_compiler("demo", "f", "gcc-2.8.0-psx", ["-O3"])
    assert net.cfg(net._project("demo")) == before


@pytest.mark.parametrize("compile_ok,matched", [(True, True), (True, False), (False, False)])
def test_apply_comparison_uses_scoped_claim_while_autopilot_holds_other_work(tmp_path, monkeypatch, compile_ok, matched):
    from decompnet.app.engine import autopilot

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    cfg = {"kind": "rom", "platform": "ps", "compiler": {"toolchain": "gcc-2.7.2-psx", "flags": ["-O2"]}}
    source = "int f(void){return 1;}"
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,?,?,?)", (pid, 1, "", ""))
        ids = [db.execute("INSERT INTO functions(project_id,name,vram,size,n_insns,status,best_score,best_source) "
                          "VALUES(?,?,?,?,?,?,?,?)", (pid, name, addr, 32, 8, "open", 200, source)).lastrowid
               for name, addr in [("f", 0x1000), ("g", 0x1100)]]
    agent, _ = net.local_agent("autopilot")
    with net.store.tx() as db:
        db.execute("INSERT INTO claims VALUES(?,?,?,?)", (ids[1], agent.id, 0, 9999999999))
    monkeypatch.setattr(net, "_judge_with", lambda *a: (False, SimpleNamespace(score=100), ""))
    compiled = []

    def compile(proj, header, candidate, fn):
        assert net.store.one("SELECT 1 FROM claims WHERE function_id=?", ids[0])
        compiled.append(candidate)
        return SimpleNamespace(ok=compile_ok, obj=b"object")

    monkeypatch.setattr(net, "_compile", compile)
    monkeypatch.setattr(net, "_evaluate", lambda *a: (SimpleNamespace(code=b"code", relocs=[]), matched, []))
    monkeypatch.setattr(net, "_target_obj", lambda *a: None)
    monkeypatch.setattr(autopilot.scoring, "diff", lambda *a: SimpleNamespace(
        score=100, max_score=200, rows=[], bytes_match=False, percent=50))
    records = []
    monkeypatch.setattr(net, "_record", lambda *a, **k: records.append((a, k)))
    net.apply_function_compiler("demo", "f", "gcc-2.8.0-psx", ["-O2"])
    assert compiled == [source]
    assert len(records) == int(compile_ok)
    if compile_ok:
        assert records[0][1]["matched"] is matched
    assert not net.store.one("SELECT 1 FROM claims WHERE function_id=?", ids[0])
    assert net.store.one("SELECT agent_id FROM claims WHERE function_id=?", ids[1])["agent_id"] == agent.id
    before = net.cfg(net._project("demo"))
    with net.store.tx() as db:
        db.execute("INSERT INTO claims VALUES(?,?,?,?)", (ids[0], agent.id, 0, 9999999999))
    monkeypatch.setattr(net, "_judge_with", lambda *a: (False, SimpleNamespace(score=1), ""))
    with pytest.raises(Exception, match="being worked on"):
        net.apply_function_compiler("demo", "f", "gcc-2.8.0-psx", ["-O3"])
    assert net.cfg(net._project("demo")) == before


def test_profile_application_reports_a_new_match_without_another_try(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    cfg = {'kind':'rom', 'platform':'ps', 'compiler':{'toolchain':'gcc-2.7.2-psx','flags':['-O2']}}
    with net.store.tx() as db:
        pid=db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                       ('demo','Demo',str(tmp_path),json.dumps(cfg))).lastrowid
        fid=db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,status,best_score,best_source) '
                       'VALUES(?,?,?,?,?,?,?,?)',(pid,'f',4096,32,8,'open',50,'int f(void){return 1;}')).lastrowid
    monkeypatch.setattr(net,'_judge_with',lambda *a:(True,SimpleNamespace(score=0),''))
    def promote(*args,**kwargs):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',best_score=0,matched_source=best_source WHERE id=?",(fid,))
    monkeypatch.setattr(net,'try_quietly',promote)
    result=net.apply_function_compiler('demo','f','gcc-2.7.2-psx',['-O3'])
    assert result['matched'] and result['newly_matched'] and result['score']==0
    assert result['match_cause']=='compiler_profile' and 'MATCH:' in result['said']
    result=net.apply_function_compiler('demo','f','gcc-2.7.2-psx',['-O3'])
    assert result['matched'] and not result['newly_matched']
    assert result['match_cause']=='already_matched'
