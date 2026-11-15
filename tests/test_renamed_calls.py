"""Calls by a function's old name rewritten to its name now (port/renamed_calls.py), on test_port_readiness'
homemade project; the console judge is stood in for (test_port_agents judges for real with KMC)."""
import pytest

from decompnet.network import NetworkError
from decompnet.port import pc_link, renamed_calls
from tests.test_pc_compile import cc  # noqa: F401  (the stand-in clang)
from tests.test_pc_link import put
from tests.test_port_readiness import project  # noqa: F401  (the fixture)


def test_calls_by_old_names_are_rewritten_and_judged(project, cc, monkeypatch):  # noqa: F811
    net, _ = project
    with pytest.raises(NetworkError, match="Compile the game's C for a PC first"):
        renamed_calls.fix(net, "game", print, cc=cc)
    # draw_hud (0x80000580) was func_80000580, then drawHud: update calls the first, helper the second
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE name='update'",
                   ("void func_80000580(void);\nvoid update(void) { func_80000580(); }",))
        db.execute("UPDATE functions SET matched_source=? WHERE name='helper'",
                   ("void drawHud(void);\nvoid helper(char *s) { drawHud(); }",))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES("
                   "(SELECT id FROM projects WHERE slug='game'),'drawHud',0x80000580,32,'func')")
    put(net, "update", ["update"], ["func_80000580"])
    put(net, "helper", ["helper"], ["drawHud"])
    put(net, "draw_hud", ["draw_hud"], [])
    judged = []

    def console(proj, header, sources=None, only=None, header_for=None, progress=None):
        judged.extend(sorted(f["name"] for f in only))
        return [("helper", "compiles but no longer matches")]  # as if its bytes moved
    monkeypatch.setattr(net, "regression_test", console)
    result = renamed_calls.fix(net, "game", print, cc=cc)
    assert judged == ["helper", "update"]  # only the callers are judged, not the whole game
    assert result["kept"] == ["update"] and result["refused"][0]["function"] == "helper"
    assert net._function("update", "game")["matched_source"] == "void draw_hud(void);\nvoid update(void) { draw_hud(); }"
    assert "drawHud()" in net._function("helper", "game")["matched_source"]  # refused: its C stays
    with net.progress.view():
        link = pc_link.report(net, net.project_row("game"))
    assert {g["kind"]: g["examples"] for g in link["groups"]}.get("renamed") == ["drawHud (now draw_hud)"]


def test_a_wrong_base_symbol_becomes_the_variable_it_reaches(project, cc, monkeypatch):  # noqa: F811
    """The decompiler counted from a label it shouldn't have (`(u8 *)&D_80010000 + 0x10`): the variable there is
    g_speed, so the C names it, and the console's bytes are the same."""
    net, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE name='update'",
                   ("extern s32 D_80010000;\nvoid update(void) { *(s32 *)((u8 *)&D_80010000 + 0x10) = 1; }",))
        db.execute("UPDATE functions SET matched_source=? WHERE name='helper'",
                   ("extern char D_80010000[];\nvoid helper(char *s) { *(s32 *)(D_80010000 + 0x12) = 2; }",))
    put(net, "update", ["update"], ["D_80010000"])
    put(net, "helper", ["helper"], ["D_80010000"])
    monkeypatch.setattr(net, "regression_test", lambda *a, **k: [])
    result = renamed_calls.fix(net, "game", print, cc=cc)
    assert result["bases"] == 2 and result["kept"] == ["helper", "update"]
    assert net._function("update", "game")["matched_source"] == (
        "extern char g_speed[];\nextern s32 D_80010000;\nvoid update(void) { *(s32 *)((u8 *)&g_speed) = 1; }")
    assert "*(s32 *)(((char *)&g_speed + 0x2)) = 2;" in net._function("helper", "game")["matched_source"]  # inside it


def test_comments_stay_and_a_static_function_others_call_is_made_global(project, cc, monkeypatch):  # noqa: F811
    """A rewrite changes code only (an earlier one rewrote a comment that described the old expression); and a
    function its own C defines static, which another function calls, loses `static`."""
    net, _ = project
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE name='update'",
                   ('extern char D_80010000[];\n/* stores at D_80010000 + 0x12, "D_80010000 + 0x12" */\n'
                    "void update(void) { *(s32 *)(D_80010000 + 0x12) = 2; }",))
        db.execute("UPDATE functions SET matched_source=? WHERE name='_allocatePVoice'",
                   ("/* static, file-local in libultra */\nstatic void _allocatePVoice(void);\n"
                    "static void _allocatePVoice(void) {}",))
    put(net, "update", ["update"], ["D_80010000"])
    put(net, "helper", ["helper"], ["_allocatePVoice"])
    put(net, "_allocatePVoice", [], [])  # compiled, exports nothing: static
    monkeypatch.setattr(net, "regression_test", lambda *a, **k: [])
    result = renamed_calls.fix(net, "game", print, cc=cc)
    assert result["statics"] == 1 and set(result["kept"]) == {"update", "_allocatePVoice"}
    update = net._function("update", "game")["matched_source"]
    assert '/* stores at D_80010000 + 0x12, "D_80010000 + 0x12" */' in update  # the comment as it was
    assert "*(s32 *)(((char *)&g_speed + 0x2)) = 2;" in update
    assert net._function("_allocatePVoice", "game")["matched_source"] == (
        "/* static, file-local in libultra */\nvoid _allocatePVoice(void);\nvoid _allocatePVoice(void) {}")
