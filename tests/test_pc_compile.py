"""Compiling a finished game's C for a PC (port/pc_compile.py), on test_port_readiness' homemade project. clang's
output is read from a stand-in compiler that answers as clang does; the pinned Zig compiles for real when it's
installed (never downloaded here)."""
import sys
import textwrap

import pytest

from decompnet import toolchains
from decompnet.port import pc_compile
from decompnet.port.readiness import describe, readiness
from tests.test_port_readiness import project  # noqa: F401  (the fixture)

# answers like clang: an asm statement doesn't assemble for x86, a pointer kept in an s32 is warned about
STAND_IN = textwrap.dedent("""
    import sys
    source = sys.argv[-1]
    text = open(source).read()
    lines = text.splitlines()
    failed = False
    for n, line in enumerate(lines, 1):
        if "asm(" in line:
            print(f"{source}:{n}:9: error: unknown register name '$31' in asm", file=sys.stderr)
            failed = True
        if "(s32)&" in line:
            print(f"{source}:{n}:12: warning: cast to smaller integer type 's32' (aka 'int') from 'int *' "
                  "[-Wpointer-to-int-cast]", file=sys.stderr)
        if "#line" in line:
            print(f"elsewhere.c:4:1: error: call to undeclared function 'later'; ISO C99 and later do not support "
                  "implicit function declarations [-Wimplicit-function-declaration]", file=sys.stderr)
            failed = True
    sys.exit(1 if failed else 0)
""")


@pytest.fixture
def cc(tmp_path):
    script = tmp_path / "clang_stand_in.py"
    script.write_text(STAND_IN, encoding="utf-8")
    return [sys.executable, str(script)]


def set_source(net, name, source):
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE name=?", (source, name))


def test_diagnostics_are_sorted_into_what_a_port_fixes():
    out = ("src/f3.c:5:9: error: unknown register name '$31' in asm\n"
           "src/f3.c:7:2: error: call to undeclared function 'osFoo'; ISO C99 and later do not support implicit "
           "function declarations [-Wimplicit-function-declaration]\n"
           "src/f3.c:8:7: error: incompatible integer to pointer conversion assigning to 'u8 *' from 's32' "
           "[-Wint-conversion]\n"
           "include/common.h:12:1: error: unknown type name 'Gfx'\n"
           "src/f3.c:9:3: warning: cast to 'void *' from smaller integer type 'u32' [-Wint-to-void-pointer-cast]\n"
           "src/f3.c:9:3: warning: using the result of an assignment as a condition [-Wparentheses]\n")
    found = pc_compile.diagnostics(out, "f3.c", 1)
    assert [d["kind"] for d in found] == ["assembly", "undeclared", "integers as pointers", "header", "pointer size"]
    assert found[0]["line"] == 4 and found[0]["file"] == ""  # the function's own line, not the file's
    assert found[3]["file"] == "common.h" and found[3]["line"] == 12
    assert pc_compile._status(found) == "failed" and pc_compile._status(found[4:]) == "warned"


def test_the_port_keeps_the_games_c_and_leaves_out_what_the_kit_replaces(project):  # noqa: F811
    net, _ = project
    with net.progress.view():
        names = {f["name"] for f in pc_compile.functions(net, net.project_row("game"))}
    assert {"update", "helper", "alSynNew", "osLooksFamiliar"} <= names  # unproven names count as the game's
    assert "boot_stub" not in names and "draw_hud" not in names  # no C


def test_every_function_is_judged_and_the_report_says_what_to_fix(project, cc):  # noqa: F811
    net, _ = project
    set_source(net, "helper", "void helper(char *s) { asm(\"move $2, $31\"); }")
    set_source(net, "alSynNew", "int x; void alSynNew(void) {\n  int k = (s32)&x;\n}")
    set_source(net, "reset_callback", "#line 1 \"elsewhere.c\"\nvoid reset_callback(void) {}")
    with net.progress.view():
        assert readiness(net, "game")["pc"] is None
        assert any("hasn't been compiled for a PC yet" in b for b in readiness(net, "game")["blockers"])
    lines = []
    result = pc_compile.check(net, "game", lines.append, cc=cc)
    assert result["failed"] == 2 and result["warned"] == 1 and result["total"] == result["ok"] + 3
    assert {k["kind"]: k["examples"] for k in result["kinds"]} == {
        "assembly": ["helper"], "undeclared": ["reset_callback"], "pointer size": ["alSynNew"]}
    assert "compile for a 64-bit PC" in lines[-1]
    with net.progress.view():
        proj = net.project_row("game")
        assert pc_compile.function(net, proj, "alSynNew")[0]["line"] == 2
        assert pc_compile.function(net, proj, "reset_callback")[0]["file"] == "elsewhere.c"
        assert pc_compile.function(net, proj, "boot_stub") is None
        report = readiness(net, "game")
    text = " ".join(report["blockers"])
    assert "2 functions don't compile for a PC: 1 assembly; 1 undeclared" in text
    assert "1 functions cast pointers to and from 32-bit integers" in text
    assert "- assembly (1): helper" in describe(report)
    # a second check replaces the first
    set_source(net, "helper", "void helper(char *s) {}")
    assert pc_compile.check(net, "game", lines.append, cc=cc)["failed"] == 1


def test_the_pinned_compiler_builds_for_a_64_bit_pc(project):  # noqa: F811
    if not toolchains.installed(pc_compile.TOOL):
        pytest.skip("The pinned Zig is not installed; never download it here.")
    net, _ = project
    set_source(net, "update", "typedef int s32;\nint x;\nvoid update(void) { s32 k = (s32)&x; }")
    set_source(net, "helper", "void helper(char *s) { later(s); }")
    result = pc_compile.check(net, "game", print)
    kinds = {k["kind"]: k["examples"] for k in result["kinds"]}
    assert kinds["pointer size"] == ["update"] and kinds["undeclared"] == ["helper"]


def test_the_same_c_compiled_twice_still_warns():
    """Zig answered a second compile of the same C from its cache, with none of the warnings: a second check, or
    check_for_pc then fix_for_pc, lost every pointer-size warning."""
    if not toolchains.installed(pc_compile.TOOL):
        pytest.skip("The pinned Zig is not installed; never download it here.")
    import uuid
    name = "v" + uuid.uuid4().hex[:8]  # new to any cache this computer has
    source = f"typedef int s32;\nint {name};\nvoid update(void) {{ s32 k = (s32)&{name}; }}"
    for _ in range(2):
        assert [d["kind"] for d in pc_compile.compile_one("", source)] == ["pointer size"]
