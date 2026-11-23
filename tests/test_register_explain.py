"""register_explain (the skill's explain_regs.py): each register the judge's rows swap, read from GCC's own dumps."""
import importlib.util
import json
from pathlib import Path

import pytest

from decompnet import hosted_judge, toolchains
from decompnet.app import compiler_passes
from decompnet.app import register_explain as rx
from tests.test_hosted import COMPILER, project

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"

LREG = """
Register 74 used 9 times across 11 insns; dies in 0 places.
Register 81 used 8 times across 8 insns in block 2.
Register 90 used 3 times across 22 insns; crosses 1 call.
Register 111 used 3 times across 5 insns.
"""
GREG = """
;; 3 regs to allocate: 74 111 90
;; 74 conflicts: 74 111 90 2 29
;; 90 conflicts: 74 90 2 3 4 29
;; 111 conflicts: 74 111 2 29
;; 111 preferences: 3 4

;; Register dispositions:
74 in 5  81 in 4  90 in 5  111 in 3

;; Hard regs used:  2 3 4 5 29
"""
ROWS = [("r", "move $a0, $zero", "move $a1, $zero"), (" ", "jr $ra", "jr $ra"),
        ("r", "addiu $a0, $a0, 4", "addiu $a1, $a1, 4")]


def test_the_dumps_are_read_with_block_locality_deaths_and_conflicts():
    lives = rx.lreg_values(LREG)
    assert lives[81]["block"] == 2 and lives[74]["block"] is None and lives[90]["calls"] == 1
    order, conflicts, prefs, disp = rx.greg_info(GREG)
    assert order == [74, 111, 90] and conflicts[90] == ({74}, {2, 3, 4, 29}) and prefs[111] == [3, 4]
    assert disp == {74: 5, 81: 4, 90: 5, 111: 3}
    assert rx.swaps(ROWS) == [("a1", "a0", 2)]
    assert rx.hard_number("$a0") == 4 and rx.hard_number("f2") == 34 and rx.hard_label(-1) == "the stack"


def test_each_swapped_register_names_its_pseudos_and_the_rule_that_decided_it():
    text = "\n".join(rx.explain(LREG, GREG, ROWS, [{"pseudo": 74, "source_lines": [2]}], "int f() {\n  x = 1;\n}"))
    assert "$a1 where the target has $a0 (2 rows)" in text
    assert "pseudo 74 -> $a1: 9 refs / 11 live, priority 2.45, global (global.c); C 2: x = 1;" in text
    assert "lower-priority conflicting pseudo (111, 0.60) prefers $a0" in text  # find_reg's regs_someone_prefers
    assert "$a0 is live across it" in text  # pseudo 90's hard conflict
    assert "pseudo 81 -> $a0" in text and "block-local, block 2 (local-alloc)" in text


def test_a_pseudo_allocated_first_into_the_wanted_register_is_named():
    greg = GREG.replace(";; 3 regs to allocate: 74 111 90", ";; 3 regs to allocate: 111 74 90")
    greg = greg.replace("74 in 5  81 in 4  90 in 5  111 in 3", "74 in 5  81 in 6  90 in 5  111 in 4")
    lines = rx.explain(LREG, greg, ROWS)
    assert any("pseudo 111 conflicts with it and was allocated first" in line for line in lines)


def test_no_register_rows_and_no_dumps_say_so():
    assert "No register-only rows" in rx.explain(LREG, GREG, [("|", "lw $a0, 0($sp)", "lh $a0, 0($sp)")])[0]
    assert "no allocation dumps" in rx.explain("", "", ROWS)[0]


def test_the_worker_answers_an_allocation_request_with_the_explanation(tmp_path, monkeypatch):
    net = project(tmp_path)
    found = {"phases": {"lreg": {"excerpt": LREG}, "greg": {"excerpt": GREG}},
             "allocation": {"values": [{"pseudo": p, "source_lines": []} for p in (74, 90, 111)]}, "toolchain": "gcc"}
    monkeypatch.setattr(compiler_passes, "diagnose", lambda *a, **k: (found, {}))
    reply = hosted_judge.allocation_report(net, {"slug": "game", "name": "f1", "source": "int f1(void){return 0;}",
                                                 "rows": [list(r) for r in ROWS]})
    assert reply["lreg"] == LREG and "why not $a0" in reply["text"]
    assert hosted_judge.allocation_report(net, {"slug": "game", "name": "f1", "source": " "}) == {"error": "no source"}


@pytest.mark.skipif(not toolchains.installed(COMPILER["toolchain"]), reason="the project's GCC isn't installed")
def test_a_real_compile_returns_the_lreg_and_greg_dumps(tmp_path):
    net = project(tmp_path)
    config = {"kind": "rom", "platform": "n64", "compiler": {"toolchain": COMPILER["toolchain"],
                                                             "flags": ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]}}
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=?", (json.dumps(config),))
    source = "int g(int);\nint f1(int a, int b) { int i, s = 0; for (i = 0; i < a; i++) { s += g(b); } return s; }"
    reply = hosted_judge.allocation_report(net, {"slug": "game", "name": "f1", "source": source,
                                                 "rows": [["r", "move $s0, $a0", "move $s1, $a0"]]})
    assert "Register " in reply["lreg"] and "regs to allocate" in reply["greg"] and "$s1 where the target" in reply["text"]


def test_the_script_prints_the_score_and_the_workers_text(capsys):
    spec = importlib.util.spec_from_file_location("explain_regs", SCRIPTS / "explain_regs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Judge:
        slug, name = "s", "f"

        def rows(self, source):
            return 20, ROWS

        def ask(self, request):
            assert request["op"] == "allocation" and request["rows"][0] == list(ROWS[0])
            return {"text": "explained"}
    assert module.explain(Judge(), "int f;")[:2] == (20, "explained")
