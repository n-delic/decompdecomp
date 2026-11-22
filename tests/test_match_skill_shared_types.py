"""shared_types.py (the match-function skill): list the project's shared header and propose, then add, a real game type
through the app's own tools (get_context / edit_context, or read_file / edit_file), refusing duplicates and conflicts.
A stub gateway stands in for the session's MCP connection: nothing here writes to a real project."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "match-function" / "scripts"


def load(name):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"skill_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load("shared_types")
setup = shared.setup

HEADER = """typedef signed char s8;
typedef unsigned char u8;
typedef int s32;
typedef float f32;
#define MAX_ACTORS 64
/* a display-list word pair */
typedef union Gfx {
    struct { s32 w0; s32 w1; } words;
    long long force_align;
} Gfx;
struct Actor;
typedef struct Actor Actor;
typedef void (*ActorFn)(Actor *);
extern s32 gActorCount;
void Actor_Update(Actor *a, f32 dt);
"""
VEC3 = "typedef struct Vec3 {\n    f32 x, y, z;\n} Vec3;\n"


class Gateway:
    """get_context / read_file / edit_context / edit_file as the app answers them."""

    def __init__(self, header=HEADER, fork=False, edit_reply="✅ header is now v7; 12 matched functions still match"):
        self.header, self.fork, self.edit_reply, self.calls = header, fork, edit_reply, []

    def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        if tool == "get_context":
            if self.fork:
                raise setup.McpError("get_context: get_context is for ROM projects. In a fork, the project's own headers "
                                     "and source files are the shared context: use read_file, search_code and edit_file")
            return "// context.h for demo — version 6 (prepended to every compile as #include \"context.h\")\n" + self.header
        if tool == "read_file":
            lines = self.header.splitlines()
            start, count = arguments["start"], arguments["count"]
            chunk = lines[start - 1:start - 1 + count]
            return "\n".join([f"{arguments['path']} (lines {start}-{start + len(chunk) - 1} of {len(lines)})"]
                             + [f"{start + i:5d}  {line}" for i, line in enumerate(chunk)])
        return self.edit_reply


def test_declarations_find_types_tags_macros_and_other_names():
    keys = {d["key"] for d in shared.declarations(HEADER)}
    assert {"typedef:s32", "typedef:Gfx", "tag:union Gfx", "tag:struct Actor", "typedef:Actor", "typedef:ActorFn",
            "macro:MAX_ACTORS", "name:gActorCount", "name:Actor_Update"} <= keys
    by = {d["key"]: d for d in shared.declarations(HEADER)}
    assert by["tag:struct Actor"]["body"] is None and by["typedef:Actor"]["body"] is None  # forward declarations
    assert by["typedef:Gfx"]["body"] and "force_align" in by["typedef:Gfx"]["body"]
    assert "a display-list" not in HEADER.replace("/* a display-list word pair */", "")  # comments are not declarations


def test_a_new_type_is_proposed_without_touching_anything():
    gateway = Gateway()
    lines = []
    assert shared.run_add(gateway, "demo", VEC3, "", "", "", False, lines.append) == 0
    text = "\n".join(lines)
    assert "Proposed for demo's shared header" in text and "new       tag:struct Vec3" in text and "typedef:Vec3" in text
    assert "A proposal only" in text
    assert [c[0] for c in gateway.calls] == ["get_context"]  # nothing written


def test_apply_appends_through_edit_context_with_a_message_and_reports_the_apps_verdict():
    gateway = Gateway()
    lines = []
    assert shared.run_add(gateway, "demo", VEC3, "func_8001", "", "", True, lines.append) == 0
    tool, args = gateway.calls[-1]
    assert tool == "edit_context" and args == {"old": "", "new": VEC3, "message": "add the shared type Vec3",
                                               "function": "func_8001"}
    assert "12 matched functions still match" in "\n".join(lines)
    assert shared.run_add(Gateway(edit_reply="❌ REJECTED: the edit breaks 2 already-matched function(s)\n- f: x"), "demo",
                          VEC3, "", "", "name Vec3", True, lines.append) == 1
    assert "REJECTED" in lines[-1]


def test_a_type_the_header_already_declares_is_a_duplicate_or_a_conflict():
    lines = []
    same = "typedef int s32;\n"
    assert shared.run_add(Gateway(), "demo", same, "", "", "", True, lines.append) == 1
    assert "already declared in the header, identically" in "\n".join(lines)
    different = "typedef long s32;\n"
    gateway = Gateway()
    lines.clear()
    assert shared.run_add(gateway, "demo", different, "", "", "", True, lines.append) == 1
    assert "already declared differently" in "\n".join(lines) and "typedef int s32" in "\n".join(lines)
    assert [c[0] for c in gateway.calls] == ["get_context"]  # refused before any write
    changed_gfx = "typedef union Gfx {\n    long long force_align;\n} Gfx;\n"
    assert shared.check(HEADER, changed_gfx)[1]
    reformatted = "typedef  int   s32 ;"
    assert "identically" in shared.check(HEADER, reformatted)[1][0]


def test_a_forward_declared_tag_may_be_defined_but_a_name_used_by_a_macro_or_variable_may_not():
    assert shared.check(HEADER, "struct Actor {\n    s32 id;\n    f32 pos[3];\n};\n")[1] == []
    assert any("used in the header as a macro" in p for p in shared.check(HEADER, "typedef s32 MAX_ACTORS;")[1])
    assert any("as a name" in p for p in shared.check(HEADER, "typedef s32 gActorCount;")[1])


def test_members_of_an_undefined_type_are_refused_but_pointers_and_tags_are_fine():
    problems = shared.check(HEADER, "typedef struct Box {\n    s23 w;\n    Vec3 *where;\n} Box;\n")[1]
    assert problems and "s23" in problems[0] and "Vec3" in problems[0]
    ok = "typedef struct Node {\n    struct Node *next;\n    Actor *owner;\n    u8 flags : 3;\n    s32 table[4], n;\n    void (*cb)(void);\n} Node;\n"
    assert shared.check(HEADER, ok)[1] == []
    later = shared.check(HEADER, "typedef struct P {\n    Vec3 a;\n} P;\n" + VEC3)[1]
    assert later and "P has members of type Vec3" in later[0]  # C needs Vec3 first
    assert shared.check(HEADER, VEC3 + "typedef struct P {\n    Vec3 a;\n} P;\n")[1] == []


def test_only_type_definitions_are_accepted_in_the_file():
    with pytest.raises(shared.TypeError_, match="type definitions only"):
        shared.check(HEADER, VEC3 + "extern Vec3 gOrigin;\n")
    with pytest.raises(shared.TypeError_, match="declares nothing"):
        shared.check(HEADER, "/* just a comment */\n")
    lines = []
    assert shared.run_add(Gateway(), "demo", "#define X 1\n", "", "", "", False, lines.append) == 1
    assert "not added" in lines[0]


def test_list_prints_the_types_and_full_prints_the_header_with_its_version():
    lines = []
    assert shared.run_list(Gateway(), "demo", "", "", False, lines.append) == 0
    text = "\n".join(lines)
    assert "version 6" in text and "typedef:Gfx" in text and "tag:struct Actor" in text and "gActorCount" not in text
    lines.clear()
    shared.run_list(Gateway(), "demo", "", "", True, lines.append)
    assert "extern s32 gActorCount;" in lines[-1]


def test_a_project_of_a_public_decomp_uses_its_header_file_through_read_file_and_edit_file():
    gateway = Gateway(fork=True)
    with pytest.raises(SystemExit, match="pass --file PATH"):
        shared.run_list(gateway, "demo", "", "", False, lambda *_: None)
    lines = []
    assert shared.run_list(gateway, "demo", "", "include/types.h", False, lines.append) == 0
    assert "typedef:Gfx" in "\n".join(lines)
    assert shared.run_add(gateway, "demo", VEC3, "", "include/types.h", "vec3 type", True, lines.append) == 0
    tool, args = gateway.calls[-1]
    assert tool == "edit_file" and args["path"] == "include/types.h" and args["old"] == "" and VEC3 in args["new"]
    assert args["message"] == "vec3 type"


def test_main_reads_the_file_and_never_applies_without_the_flag(tmp_path, capsys):
    types = tmp_path / "vec3.h"
    types.write_text(VEC3)
    gateway = Gateway()
    assert shared.main(["add", "demo", str(types)], gateway=gateway) == 0
    assert [c[0] for c in gateway.calls] == ["get_context"]
    assert shared.main(["add", "demo", str(types), "--apply", "-m", "Vec3"], gateway=gateway) == 0
    assert gateway.calls[-1][0] == "edit_context" and gateway.calls[-1][1]["message"] == "Vec3"
    with pytest.raises(SystemExit, match="can't read"):
        shared.main(["add", "demo", str(tmp_path / "missing.h")], gateway=gateway)
    assert shared.main(["list", "demo"], gateway=Gateway()) == 0
    assert "typedef:Gfx" in capsys.readouterr().out


def test_a_refused_call_is_reported():
    class Refusing:
        def call(self, tool, arguments):
            raise setup.McpError("get_context: no project demo")

    with pytest.raises(SystemExit, match="no project demo"):
        shared.main(["list", "demo"], gateway=Refusing())
