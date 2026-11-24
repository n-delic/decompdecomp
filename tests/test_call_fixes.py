"""Functions their callers disagree with, rewritten the way they're called (port/call_fixes.py), on
test_port_readiness' homemade project; the console judge is stood in for."""
import json

import pytest

from decompnet.network import NetworkError
from decompnet.port import call_fixes
from decompnet.port.wasm import play
from tests.test_port_readiness import project  # noqa: F401  (the fixture)


def test_a_void_draft_returns_its_trailing_call_as_callers_expect():
    src = "M2C_UNK act();\n\nvoid isFire(void *arg0) {\n    act(arg0, 8);\n}\n"
    out = call_fixes.rewrite(src, "isFire", ("s32", ["Ctl *pThis"]), passed=None, takes_result=True, whole=True)
    assert "s32 isFire(Ctl *arg0) {\n    return act(arg0, 8);" in out
    # no trailing call: nothing says what $v0 holds, so no rewrite
    assert call_fixes.rewrite("void f(s32 a) {\n    x = a;\n}\n", "f", None, passed=None, takes_result=True,
                              whole=False) is None


def test_arguments_callers_pass_are_declared_and_unused_ones_they_leave_out_go():
    more = call_fixes.rewrite("void ev(s32 arg0, Vec arg1) {\n    foo(arg0);\n}\n", "ev", None, passed=(3, 3),
                              takes_result=False, whole=False)
    assert more.startswith("void ev(s32 arg0, Vec arg1, s32 arg2) {")
    fewer = call_fixes.rewrite("void e(s32 arg0, s32 arg1, s32 arg2) {\n    foo(arg0, arg1);\n}\n", "e", None,
                               passed=(2, 2), takes_result=False, whole=False)
    assert fewer.startswith("void e(s32 arg0, s32 arg1) {")
    # an argument it reads that a caller leaves out stays: only the console's leftover register gives it
    assert call_fixes.rewrite("void e(s32 arg0, s32 arg1) {\n    foo(arg1);\n}\n", "e", None, passed=(1, 1),
                              takes_result=False, whole=False) is None


def test_prototypes_are_read_from_declarations():
    assert call_fixes.prototype("extern float isFire (Ctl *pThis);", "isFire") == ("float", ["Ctl *pThis"])
    assert call_fixes.prototype("s32 *f(s32, void *);", "f") == ("s32 *", ["s32", "void *"])
    assert call_fixes.prototype("s32 f();", "f") == ("s32", None)
    assert call_fixes.prototype("    x = f(1);", "f") is None


def test_each_rewrite_is_judged_and_only_matches_are_kept(project, monkeypatch, tmp_path):  # noqa: F811
    net, _ = project
    monkeypatch.setattr(play, "game", lambda net, slug: (tmp_path, {}, None))
    with pytest.raises(NetworkError, match="Build the game for the browser first"):
        call_fixes.fix(net, "game", print)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE name='update'",
                   ("s32 helper(char *s);\nvoid update(void) { if (helper(0)) draw_hud(1); }",))
        db.execute("UPDATE functions SET matched_source=? WHERE name='helper'",
                   ("void helper(void *arg0) {\n    draw_hud(arg0);\n}",))
        db.execute("UPDATE functions SET status='matched', matched_source=? WHERE name='draw_hud'",
                   ("void draw_hud(void) {\n}",))
    build = tmp_path / "port" / "wasm" / "build"
    build.mkdir(parents=True)
    (build / "calls.json").write_text(json.dumps({"update": [
        {"callee": "helper", "caller": "update", "kind": "void-result"},
        {"callee": "draw_hud", "caller": "update", "kind": "extra-arguments", "passed": 1, "declared": 0}]}))
    judged = []

    def console(proj, header, sources=None, only=None, header_for=None, progress=None):
        judged.extend(sorted(f["name"] for f in only))
        return [("draw_hud", "compiles but no longer matches")]  # as if its bytes moved
    monkeypatch.setattr(net, "regression_test", console)
    result = call_fixes.fix(net, "game", print)
    assert judged[:2] == ["draw_hud", "helper"]  # the callees, not their callers
    assert result["kept"] == ["helper"] and result["calls"] == 1
    assert net._function("helper", "game")["matched_source"] == \
        "s32 helper(char *arg0) {\n    return draw_hud(arg0);\n}"
    assert net._function("draw_hud", "game")["matched_source"] == "void draw_hud(void) {\n}"  # refused: stays
    assert [r["function"] for r in result["refused"]] == ["draw_hud"]
    # the next report lists each function still disagreeing with why its C was kept, for the Porting tab's drawer
    from decompnet.port.wasm import calls
    listed = {c["name"]: c for c in calls.load(build)["callees"]}
    assert listed["draw_hud"]["refused"] == "the console build compiles but no longer matches"
    assert listed["draw_hud"]["calls"] == 1 and listed["draw_hud"]["callers"] == 1
    assert listed["helper"]["refused"] == "" and listed["helper"]["kinds"] == ["void-result"]


def test_callers_types_are_read_as_the_callees_file_names_them():
    text = "typedef struct Thing Thing;\nstruct Known { int x; };\n"
    assert call_fixes.local_type("M2C_UNK", text) == "s32"
    assert call_fixes.local_type("M2C_UNK16 *", text) == "s16 *"
    assert call_fixes.local_type("Thing *", text) == "Thing *"
    assert call_fixes.local_type("CGameObjectInstance *", text) == "void *"
    assert call_fixes.local_type("BOOL", text) is None
    assert call_fixes.local_type("unsigned char", text) == "unsigned char"
    view = call_fixes._local_view(("M2C_UNK", ["CGameObjectInstance *pThis", "BOOL on"]), text)
    assert view == ("s32", None)
    assert call_fixes._local_view(("float", ["Other *p"]), text) == ("float", ["void *p"])


def test_a_result_falls_back_to_the_trailing_calls_own_type():
    src = "s32 g(void *a);\nvoid f(void *a) {\n    g(a);\n}\n"
    assert call_fixes._trailing_type(src, "f", src) == "s32"
    assert call_fixes._trailing_type("void f(void) {\n    h();\n}\n", "f", "") == "s32"
    assert call_fixes._trailing_type("u8 h(void);\nvoid f(void) {\n    h();\n}\n", "f",
                                     "u8 h(void);\n") == "u8"
