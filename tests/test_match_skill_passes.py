"""passes.py (the match-function skill) prints the compiler's allocation table, register preferences and scheduling
orders through the same code as the compiler_passes tool."""
import importlib.util
import json
import stat
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import hosted_judge, toolchains
from decompnet.app import compiler_passes
from tests.test_hosted import COMPILER, project

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"

GREG = """
;; 3 regs to allocate: 85 72 73
;; 72 conflicts: 72 73 85
;; 72 preferences: 4
;; 73 conflicts: 72 73 85
;; 73 preferences: 5
;; 85 conflicts: 72 73 85

;; Register dispositions:
72 in 4  73 in 5  85 in 3
"""
SCHED = """
(insn 4 2 6 (set (reg/v:SI 72)
        (reg:SI 4 a0)) 159 {movsi_internal2} (nil)
    (nil))

(note 7 6 8 "" NOTE_INSN_FUNCTION_BEG)

(jump_insn 58 57 63 (set (pc)
        (if_then_else (le:SI (reg/v:SI 72)
                (const_int 0))
            (label_ref 47)
            (pc))) 221 {branch_zero} (nil)
    (nil))
"""


def result():
    return {"phases": {"greg": {"excerpt": GREG}, "sched": {"excerpt": SCHED}},
            "allocation": {"values": [{"pseudo": 72, "aliases": ["a"], "hard_register": 4, "allocation_rank": 0, "references": 4,
                                       "live_insns": 11, "legacy_weight": 0.73},
                                      {"pseudo": 85, "aliases": [], "hard_register": 3, "allocation_rank": 1, "references": 7,
                                       "live_insns": 8, "legacy_weight": 1.75}]}}


def test_preferences_name_the_pseudo_and_what_it_got():
    lines = compiler_passes.preferences(result())
    assert lines[0] == "pseudo 72 (a) prefers hard reg 4, got 4"
    assert lines[1] == "pseudo 73 (temp) prefers hard reg 5"  # not in the allocation table: nothing to compare


def test_conflicts_name_the_hard_registers_a_pseudo_is_live_with():
    greg = ";; 2 regs to allocate: 80 77\n;; 77 conflicts: 77 80 2 4 29\n;; 80 conflicts: 77 80 4 5 6\n;; 80 preferences: 2 7\n"
    lines = compiler_passes.conflicts({"phases": {"greg": {"excerpt": greg}}, "allocation": {"values": [
        {"pseudo": 80, "aliases": ["a3"]}]}})
    assert lines == ["pseudo 77 (temp) is live with hard regs 2 4 29", "pseudo 80 (a3) is live with hard regs 4 5 6"]
    assert compiler_passes.conflicts({}) == []


def test_the_scheduling_order_lists_insns_in_the_dumps_order_without_notes():
    assert compiler_passes.schedule_order(result(), "sched") == ["4 movsi_internal2", "58 branch_zero"]
    assert compiler_passes.schedule_order(result(), "sched2") == []


def test_the_worker_answers_a_passes_request_and_reports_a_missing_compiler(tmp_path, monkeypatch):
    net = project(tmp_path)
    monkeypatch.setattr(compiler_passes, "diagnose", lambda *a, **k: (result() | {"toolchain": "gcc-2.7.2-kmc"}, {}))
    reply = hosted_judge.passes_report(net, {"slug": "game", "name": "f1", "source": "int f1(void){return 0;}"})
    assert "prefers hard reg 4" in reply["text"] and "Scheduling order, sched" in reply["text"]
    assert "is live with hard regs" in reply["text"]
    assert hosted_judge.passes_report(net, {"slug": "game", "name": "f1", "source": " "}) == {"error": "no source"}
    assert "no project" in hosted_judge.passes_report(net, {"slug": "x", "name": "f1", "source": "a"})["error"]


@pytest.mark.skipif(not toolchains.installed(COMPILER["toolchain"]), reason="the project's GCC isn't installed")
def test_a_real_compile_prints_the_allocation_table_preferences_and_orders(tmp_path):
    net = project(tmp_path)
    config = {"kind": "rom", "platform": "n64", "compiler": {"toolchain": COMPILER["toolchain"], "flags": ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]}}
    with net.store.tx() as db:  # the shared project names a command that doesn't exist here; the real compiler runs instead
        db.execute("UPDATE projects SET config=?", (json.dumps(config),))
    source = "int f1(int a, int b) { int i, s = 0; for (i = 0; i < a; i++) { s += b; } return s; }"
    text = hosted_judge.passes_report(net, {"slug": "game", "name": "f1", "source": source})["text"]
    assert "Register allocation" in text and "prefers hard reg" in text and "Scheduling order, sched2" in text


STUB = textwrap.dedent("""\
    #!{python}
    import json, sys
    for line in sys.stdin:
        r = json.loads(line)
        out = ({{"name": "f", "vram": 1, "target": "00", "header": "", "compiler": {{"toolchain": "t", "flags": []}}}}
               if r["op"] == "function" else {{"text": "passes for " + ",".join(r.get("passes", ["default"])) + (" verbose" if r.get("sched_verbose") else "")}})
        print(json.dumps(out), flush=True)
""")


def test_passes_py_asks_the_worker_and_prints_its_text(tmp_path, monkeypatch, capsys):
    stub = tmp_path / "decomp"
    stub.write_text(STUB.format(python=sys.executable))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("DECOMP_BIN", str(stub))
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("skill_passes", SCRIPTS / "passes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "c.c").write_text("int f(void){return 0;}")
    module.main([str(tmp_path / "c.c"), "--slug", "g", "--function", "f", "--passes", "greg,sched2"])
    assert capsys.readouterr().out.strip() == "passes for greg,sched2"
    module.main([str(tmp_path / "c.c"), "--slug", "g", "--function", "f", "--passes", "sched", "--sched-verbose"])
    assert capsys.readouterr().out.strip() == "passes for sched verbose"


def test_the_skills_report_lists_every_pseudo_not_just_the_first_sixteen():
    values = [{"pseudo": 100 + i, "aliases": [], "hard_register": 16 + i % 8, "allocation_rank": i, "references": 4,
               "live_insns": 10 + i, "legacy_weight": 0.5} for i in range(20)]
    values.append({"pseudo": 200, "aliases": [], "hard_register": -1, "allocation_rank": 20})
    text = compiler_passes.render({"allocation": {"values": values}}, (), "candidate", rows=None)
    assert "pseudo 119" in text and "pseudo 200 (temp) -> spilled to the stack" in text
    assert "pseudo 119" not in compiler_passes.render({"allocation": {"values": values}}, (), "candidate")


VERBOSE = SCHED + """
;; ready list (t = 3): 58:2 4:1
;; launching insn 58 (priority 2)
;; set-count rule: insn 4 first
"""


def test_the_verbose_scheduler_notes_need_no_compiler_flag_and_print_only_the_notes(tmp_path, monkeypatch):
    net = project(tmp_path)
    seen = {}

    def diagnose(net, proj, fn, source, passes, excerpt_chars=None, extra_flags=()):
        seen.update(passes=passes, extra_flags=extra_flags)
        return result() | {"toolchain": "gcc-2.7.2-kmc", "phases": {"sched": {"excerpt": VERBOSE}}}, {}
    monkeypatch.setattr(compiler_passes, "diagnose", diagnose)
    plain = hosted_judge.passes_report(net, {"slug": "game", "name": "f1", "source": "int f1(void){return 0;}"})["text"]
    assert seen["extra_flags"] == () and "-fsched-verbose-2" not in plain
    text = hosted_judge.passes_report(net, {"slug": "game", "name": "f1", "source": "int f1(void){return 0;}",
                                            "passes": ["greg"], "sched_verbose": True})["text"]
    assert seen["extra_flags"] == () and "sched" in seen["passes"]  # cc1 2.7.2 refuses -fsched-verbose-2
    assert "First scheduler notes (3 lines):" in text
    assert ";; launching insn 58 (priority 2)" in text and ";; set-count rule: insn 4 first" in text
    assert compiler_passes.verbose_schedule({"phases": {}}) == []


LOOP = """;; Function f
Loop from 17 to 57: 13 real insns.
Insn 33: regno 90 (life 4), savings 2  moved to 73
(insn 33 31 35 (set (reg:SI 90)
        (const_int 7)) 1 {movsi} (nil)
    (nil))
Biv 83 initialized at insn 16: initial value 0
giv at 43 reduced to (reg:SI 101)
"""


def test_the_loop_optimizers_notes_are_read_out_of_the_loop_dump_without_the_rtl():
    assert compiler_passes.loop_decisions({"phases": {"loop": {"excerpt": LOOP}}}) == [
        "Loop from 17 to 57: 13 real insns.", "Insn 33: regno 90 (life 4), savings 2  moved to 73",
        "Biv 83 initialized at insn 16: initial value 0", "giv at 43 reduced to (reg:SI 101)"]
    assert compiler_passes.loop_decisions({}) == []
    many = {"phases": {"loop": {"excerpt": "\n".join(f"Insn {i}: moved to {i}" for i in range(100))}}}
    assert compiler_passes.loop_decisions(many)[-1] == "... 20 more in the loop dump"


def test_the_report_has_the_loop_section_and_hands_back_the_compile_files(tmp_path, monkeypatch):
    net = project(tmp_path)
    kept = {"i": "int f1(void){return 0;}", "s": "f1:\n\tjr\t$31\n", "lreg": ";; Function f1\n"}
    monkeypatch.setattr(compiler_passes, "diagnose", lambda *a, **k: (
        result() | {"toolchain": "sn-n64-281", "files": kept, "phases": {"loop": {"excerpt": LOOP}}}, {}))
    reply = hosted_judge.passes_report(net, {"slug": "game", "name": "f1", "source": "int f1(void){return 0;}", "save": True})
    assert "Loop optimizer decisions" in reply["text"] and "  Insn 33: regno 90" in reply["text"]
    assert reply["files"] == kept
    assert "files" not in hosted_judge.passes_report(net, {"slug": "game", "name": "f1", "source": "int f1(void){return 0;}"})


def test_passes_py_save_writes_every_file_beside_the_report(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("skill_passes_save", SCRIPTS / "passes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    line = module.save_files(tmp_path / "out", "f", "the report", {"i": "C", "s": "asm", "lreg": "alloc"})
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["f.i", "f.lreg", "f.report.txt", "f.s"]
    assert (tmp_path / "out" / "f.report.txt").read_text() == "the report" and "f.s" in line


def test_sn_profiles_are_diagnosable_through_their_driver():
    from decompnet.app import compiler_diagnostics
    for tid in ("sn-n64-281", "sn-n64-272-0001", "sn-n64-272-0001-as0004", "sn-n64-272-0004", "sn-n64-272-0006", "sn-n64-272-970404"):
        assert toolchains.get(tid).pass_dumps, tid
    assert not toolchains.get("gcc-2.7.2-kmc").pass_dumps  # a native driver needs no flag
    cfg = SimpleNamespace(cmd=["py", "sn64.py", "--cc", "/t/cc1n64.exe", "--cpp", "/t/cppn64.exe", "--runner", "/t/wibo",
                               "{src}", "{obj}", "{include}", "-G0", "-O2", "-DX=1", "-I/inc"])
    assert compiler_diagnostics.driver_cc1_words(cfg) == ["/t/wibo", "/t/cc1n64.exe", "-quiet", "-G0", "-O2", "IN.i", "-o", "OUT.s"]
    assert compiler_diagnostics.driver_cc1_words(SimpleNamespace(cmd=["gcc", "-c"])) is None


def test_kept_files_name_each_by_its_last_extension_and_skip_objects(tmp_path):
    from decompnet.app import compiler_diagnostics
    for name, text in {"func.c": "src", "func.sn.i": "pre", "func.sn.s": "a\r\nb\r\n", "func.sn.i.lreg": "L", "func.sn.i.addressof": "x"}.items():
        (tmp_path / name).write_bytes(text.encode())
    (tmp_path / "func.o").write_bytes(b"\x7fELF")
    (tmp_path / "func.sn.obj").write_bytes(b"obj")
    assert compiler_diagnostics.kept_files(tmp_path) == {"c": "src", "i": "pre", "lreg": "L", "s": "a\nb\n"}


SN = "sn-n64-281"


@pytest.mark.skipif(not toolchains.installed(SN) or not toolchains.installed("wibo") or not toolchains.installed("psyq-obj-parser"),
                    reason="the SN64 compiler, wibo or the converter isn't installed")
def test_a_real_sn_compile_gives_the_passes_loop_notes_and_files(tmp_path):
    net = project(tmp_path)
    config = {"kind": "rom", "platform": "n64", "compiler": {"toolchain": SN, "flags": toolchains.get(SN).flags}}
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=?", (json.dumps(config),))
    source = "extern int g; int f1(int *p, int a, int n) { int i, s = 0; for (i = 0; i < n; i++) { s += p[i] * (a * 7 + g); } return s; }"
    reply = hosted_judge.passes_report(net, {"slug": "game", "name": "f1", "source": source, "save": True})
    assert "Register allocation" in reply["text"] and "Loop optimizer decisions" in reply["text"] and "moved to" in reply["text"]
    assert "Scheduling order, sched2" in reply["text"]
    assert {"i", "s", "lreg", "greg", "loop", "sched", "sched2"} <= set(reply["files"])
    assert "f1:" in reply["files"]["s"] and "f1" in reply["files"]["i"]
    pre = hosted_judge.preprocess_report(net, {"slug": "game", "name": "f1", "source": source})
    assert "f1" in pre["text"] and pre["cc1"][-3:] == ["IN.i", "-o", "OUT.s"] and "-quiet" in pre["cc1"]


@pytest.mark.skipif(not toolchains.installed(SN) or not toolchains.installed("wibo") or not toolchains.installed("psyq-obj-parser"),
                    reason="the SN64 compiler, wibo or the converter isn't installed")
def test_a_real_sn_compile_answers_the_allocation_request_handoff_and_explain_regs_use(tmp_path):
    net = project(tmp_path)
    config = {"kind": "rom", "platform": "n64", "compiler": {"toolchain": SN, "flags": toolchains.get(SN).flags}}
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=?", (json.dumps(config),))
    source = "int f1(int a, int b) { int i, s = 0; for (i = 0; i < a; i++) { s += b * i; } return s; }"
    reply = hosted_judge.allocation_report(net, {"slug": "game", "name": "f1", "source": source, "rows": []})
    assert "error" not in reply and "Register " in reply["lreg"] and reply["allocation"]  # -g (refused or not) costs nothing
