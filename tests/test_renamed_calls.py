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
