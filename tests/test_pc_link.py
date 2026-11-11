"""What stops the game's C linking into one PC program (port/pc_link.py), from the symbols Compile for PC records
for each function, on test_port_readiness' homemade project."""
import json

import pytest

from decompnet import toolchains
from decompnet.port import pc_compile, pc_link
from decompnet.port.readiness import describe, readiness
from tests.test_port_readiness import project  # noqa: F401  (the fixture)


def put(net, name, defines=(), needs=(), compiled=True):
    with pc_compile._store(net, net.project_row("game")).tx() as db:
        pc_compile.record(db, net.project_row("game")["id"], name, [] if compiled else
                          [{"level": "error", "kind": "other", "line": 1, "file": "", "message": "x"}],
                          symbols={"defines": list(defines), "needs": list(needs)} if compiled else None)


def test_each_undefined_symbol_is_sorted_by_what_supplies_it(project):  # noqa: F811
    net, _ = project
    with net.progress.view():
        assert pc_link.report(net, net.project_row("game")) is None
    # update calls draw_hud's old automatic name (it's at 0x80000580), reads two data symbols; helper calls the
    # game's assembly, a libultra function the kit replaces, an unmatched function and something nobody names
    put(net, "update", ["update"], ["helper", "func_80000580", "D_80010000", "g_speed", "drawHud"])
    put(net, "helper", ["helper", "shared_table"], ["boot_stub", "osInvalDCache", "draw_hud", "mystery",
                                                    "_allocatePVoice", "osCreateThread"])
    # _allocatePVoice's C defines it static (libultra's file-local helper): compiled, but it exports nothing
    put(net, "_allocatePVoice", [], [])
    with net.store.tx() as db:  # draw_hud was called drawHud before: the symbol table keeps that name too
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES("
                   "(SELECT id FROM projects WHERE slug='game'),'drawHud',0x80000580,32,'func')")
    put(net, "alSynNew", ["alSynNew", "shared_table"], [])
    put(net, "reset_callback", compiled=False)  # no object: counts as defining itself
    with net.progress.view():
        link = pc_link.report(net, net.project_row("game"))
    groups = {g["kind"]: g["examples"] for g in link["groups"]}
    assert groups == {"renamed": ["drawHud (now draw_hud)", "func_80000580 (now draw_hud)"],
                      "static": ["_allocatePVoice"], "assembly": ["boot_stub"],
                      "not matched": ["draw_hud"], "data": ["D_80010000", "g_speed"], "glue": ["osCreateThread"],
                      "kit": ["osInvalDCache"],
                      "other": ["mystery"]}
    assert [g["kind"] for g in link["groups"]] == list(pc_link.GROUPS)  # the order fixes come in
    assert (link["undefined"], link["provided"], link["unread"], link["functions"]) == (9, 1, 1, 5)
    assert link["duplicates"] == [{"name": "shared_table", "in": ["alSynNew", "helper"]}]
    with net.progress.view():
        report = readiness(net, "game")
    text = " ".join(report["blockers"])
    assert "the C would need 9 symbols nothing defines: 2 renamed; 1 static; 1 assembly" in text
    assert "1 defined twice" in text and "1 kit" not in text and "- renamed (2, needed by 1 functions)" in describe(report)


def test_data_already_in_c_counts_as_defined(project):  # noqa: F811
    net, _ = project
    put(net, "update", ["update"], ["D_80010000"])
    from decompnet.app import data_units  # a matched data unit holds 0x80010000
    with data_units._store(net, net.project_row("game")).tx() as db:
        db.execute("INSERT INTO data_units(project_id, address, size, span, status) VALUES(?,?,?,?,?)",
                   (net.project_row("game")["id"], 0x80010000, 16, 16, "matched"))
    with net.progress.view():
        assert pc_link.report(net, net.project_row("game"))["undefined"] == 0


def test_the_pinned_compiler_reports_what_an_object_defines_and_needs():
    if not toolchains.installed(pc_compile.TOOL):
        pytest.skip("The pinned Zig is not installed; never download it here.")
    linked = {}
    pc_compile.compile_one("extern int g_speed; int later(int);",
                           "int table[4];\nint update(void) { return later(g_speed) + table[1]; }", linked=linked)
    assert linked["symbols"] == {"defines": ["table", "update"], "needs": ["g_speed", "later"]}
    broken = {}
    pc_compile.compile_one("", "int update(void) { return nope; }", linked=broken)
    assert broken["symbols"] is None
    assert json.dumps(linked["symbols"])
