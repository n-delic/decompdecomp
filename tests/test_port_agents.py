"""Matched C fixed for the PC and judged twice (port/port_fix.py), and the agents that do it (app/port_agents.py), on
test_data_units' homemade N64 project. The console side is judged for real by the pinned KMC GCC (tests skip
without it); the PC side by a stand-in that answers as clang does."""
import hashlib
import json

import pytest

from decompnet import toolchains
from decompnet.app import port_agents
from decompnet.network import NetworkError
from decompnet.port import pc_compile, port_fix
from tests.test_data_units import TEXT, build
from tests.test_pc_compile import STAND_IN

ADDRESS = TEXT + 0x20
# a pointer kept in an s32: matches on the console, loses half of itself on a 64-bit PC
BEFORE = "typedef int s32;\ns32 get_first(s32 table) { return *(s32 *)table; }"
FIXED = "typedef int s32;\ns32 get_first(s32 *table) { return *table; }"
SLOWER = "typedef int s32;\ns32 get_first(s32 *table) { s32 v = *table; return v + 0; }"  # a different body
CHANGED = "typedef int s32;\ns32 get_first(s32 *table) { return table[1]; }"  # other bytes on the console

# the stand-in warns of the pointer cast, as clang does
WARNING = STAND_IN.replace('''    if "#line" in line:''', '''    if "*)table" in line:
        print(f"{source}:{n}:30: warning: cast to 's32 *' (aka 'int *') from smaller integer type 's32' "
              "[-Wint-to-pointer-cast]", file=sys.stderr)
    if "#line" in line:''')
# and refuses a call the shared header doesn't declare (a comment stands for the call: it changes no console bytes)
WARNING = WARNING.replace('''    if "#line" in line:''', '''    if "needs_proto" in line and "needs_proto" not in open("include/common.h").read():
        print(f"{source}:{n}:1: error: call to undeclared function 'needs_proto'; ISO C99 and later do not "
              "support implicit function declarations [-Wimplicit-function-declaration]", file=sys.stderr)
        failed = True
    if "#line" in line:''')
assert WARNING != STAND_IN and "needs_proto" in WARNING


@pytest.fixture
def project(tmp_path, monkeypatch):
    if not toolchains.installed("gcc-2.7.2-kmc"):
        pytest.skip("Pinned KMC compiler is not installed; never download it here.")
    net = build(tmp_path)
    proj = net.project_row("game")
    compiled = net._compile(proj, "", BEFORE, {"name": "get_first", "vram": ADDRESS, "unit": ""})
    assert compiled.ok, compiled.log
    from decompnet.elfobj import extract_function
    code = extract_function(compiled.obj, "get_first").code
    rom = bytearray((tmp_path / "game.z64").read_bytes())  # the game's bytes are what KMC made of it
    rom[0x1020:0x1020 + len(code)] = code
    (tmp_path / "game.z64").write_bytes(rom)
    cfg = net.cfg(proj) | {"rom": net.cfg(proj)["rom"] | {"sha1": hashlib.sha1(rom).hexdigest()}}
    with net.store.tx() as db:
        db.execute("UPDATE projects SET config=? WHERE id=?", (json.dumps(cfg), proj["id"]))
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,vram,status,matched_source,target,relocs,meta) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)", (proj["id"], "get_first", len(code), len(code) // 4, ADDRESS,
                                                   "matched", BEFORE, code, "[]", "{}"))
        db.execute("INSERT INTO symbols(project_id,name,address,size,kind) VALUES(?,?,?,?,?)",
                   (proj["id"], "get_first", ADDRESS, len(code), "func"))
    script = tmp_path / "clang_stand_in.py"
    script.write_text(WARNING, encoding="utf-8")
    import sys
    cc = [sys.executable, str(script)]
    monkeypatch.setattr(pc_compile, "compiler", lambda log=print: cc)  # the PC judge's, for these tests
    yield net, cc
    if hasattr(net, "autosave"):
        net.autosave.close()


def agent(net, name="claude-opus-5.5-1"):
    return net.local_agent(name)[0]


def test_a_fix_is_kept_only_when_both_builds_agree(project):
    net, cc = project
    me = agent(net)
    with pytest.raises(NetworkError, match="hasn't been compiled for a PC yet"):
        port_fix.fix(net, me, "game", "get_first", FIXED)
    pc_compile.check(net, "game", print, cc=cc)
    with net.progress.view():
        assert pc_compile.function(net, net.project_row("game"), "get_first")[0]["kind"] == "pointer size"
    assert "1 pointer-size warnings" in port_fix.check(net, "game", "get_first", BEFORE)
    assert "0 errors, 0 pointer-size" in port_fix.check(net, "game", "get_first", FIXED)

    refused = port_fix.fix(net, me, "game", "get_first", CHANGED)
    assert not refused["kept"] and "console build no longer matches" in refused["text"]
    assert net._function("get_first", "game")["matched_source"] == BEFORE
    worse = port_fix.fix(net, me, "game", "get_first", BEFORE.replace("{ return", "{\n(void)(s32 *)table;\nreturn"))
    assert not worse["kept"] and "more pointers" in worse["text"]

    kept = port_fix.fix(net, me, "game", "get_first", FIXED)
    assert kept["kept"], kept["text"]
    assert net._function("get_first", "game")["matched_source"] == FIXED
    with net.progress.view():
        proj = net.project_row("game")
        assert pc_compile.function(net, proj, "get_first") == []
        assert pc_compile.summary(net, proj)["warned"] == 0
    # nothing left to improve: the same verdict again isn't a fix
    again = port_fix.fix(net, me, "game", "get_first", SLOWER)
    assert not again["kept"] and "says the same of it" in again["text"]
    with pytest.raises(NetworkError, match="the C it has already"):
        port_fix.fix(net, me, "game", "get_first", FIXED)


def test_a_header_fix_and_a_big_function_fixed_a_step_at_a_time_are_kept(project):
    net, cc = project
    me = agent(net)
    # two pointers kept in s32s and a call the header doesn't declare: 1 error, 2 warnings
    both = BEFORE.replace("{ return *(s32 *)table; }", "{\n/* needs_proto */\ns32 a = *(s32 *)table;\n"
                                                         "return a + *(s32 *)table - a; }")
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE name='get_first'", (both,))
    pc_compile.check(net, "game", print, cc=cc)
    # the error is fixed and one warning is left: better on one count, no worse on the other, so it's kept and the
    # function stays in the queue (it doesn't have to compile for a PC at once)
    half = both.replace("/* needs_proto */", "/* the call, declared */").replace("s32 a = *(s32 *)table", "s32 a = 0")
    step = port_fix.fix(net, me, "game", "get_first", half)
    if not step["kept"]:  # the console bytes moved: try the step the guide gives, the PC's lines behind PORT
        pytest.skip("the homemade function's bytes changed: " + step["text"][:200])
    assert "closer to building for a PC" not in step["text"] and "1 pointer-size warnings left" in step["text"]
    with net.progress.view():
        assert port_agents.queue(net, net.project_row("game")) == ["get_first"]
    # back to the C that needs the declaration; then the header alone fixes it, and the C is submitted unchanged
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE name='get_first'", (both,))
    pc_compile.check(net, "game", print, cc=cc)
    with pytest.raises(NetworkError, match="the C it has already"):
        port_fix.fix(net, me, "game", "get_first", both)
    with net.store.tx() as db:  # the header gets the declaration (edit_context judges that edit on its own)
        db.execute("INSERT INTO context_versions(project_id, version, header, message, created_at, unit) "
                   "VALUES((SELECT id FROM projects WHERE slug='game'), 1, '/* void needs_proto(void); */', "
                   "'declare it', 0, '')")
    header = port_fix.fix(net, me, "game", "get_first", both)
    assert header["kept"] and "the header's change fixed it" in header["text"], header["text"]
    assert net._function("get_first", "game")["matched_source"] == both
    with net.progress.view():
        assert pc_compile.function(net, net.project_row("game"), "get_first")[0]["kind"] == "pointer size"


def test_agents_claim_fix_and_release_one_function_at_a_time(project):
    net, cc = project
    first, second = agent(net), agent(net, "claude-opus-5.5-2")
    with pytest.raises(ValueError, match="Compile the game's C for a PC first"):
        port_agents.configure(net, "game", {"task": "port"}, 1)
    with net.store.tx() as db:  # boot's C can't be compiled for a PC by a missing declaration (the stand-in's #line rule)
        db.execute("UPDATE functions SET matched_source=? WHERE name='boot'", ("#line 1 \"x.c\"\nvoid boot(void) {}",))
    pc_compile.check(net, "game", print, cc=cc)
    assert port_agents.configure(net, "game", {"task": "port"}, 1) == {"task": "port"}
    assert port_agents.left(net, "game") == 2
    brief = port_agents.claim(net, first, "game")
    assert brief.startswith("Claimed boot (2 functions left)")  # what doesn't compile comes first
    assert "[undeclared]" in brief and "void boot(void)" in brief
    assert port_agents.claim(net, second, "game").startswith("Claimed get_first")
    assert port_agents.claim(net, second, "game", "boot") == "Another agent holds that function."

    class Session:
        options = {"task": "port"}
    port_agents.bind(net, Session(), second)
    with pytest.raises(NetworkError, match="Claim this function first"):
        port_agents.guard(net, second, "game", "fix_for_pc", {"function": "boot"})
    with pytest.raises(NetworkError, match="isn't one of their tools"):
        port_agents.guard(net, second, "game", "compile_and_diff", {})
    port_agents.guard(net, second, "game", "fix_for_pc", {"function": "get_first"})
    text = port_agents.fixed(net, second, "game", "get_first", port_fix.fix(net, second, "game", "get_first", FIXED))
    assert text.startswith("✅ get_first builds for a PC") and text.endswith("claim_port_fix() for the next function.")
    assert port_agents.left(net, "game") == 1

    assert port_agents.release(net, first, "game", "boot", "it needs a prototype").startswith("Released boot")
    assert "A previous try left this note:\nit needs a prototype" in port_agents.claim(net, second, "game")
    pc_compile.check(net, "game", print, cc=cc)  # a new check keeps the tries and the note
    with net.progress.view():
        row = pc_compile._store(net, net.project_row("game")).one(
            "SELECT attempts, note FROM port_compile WHERE name='boot'")
    assert row["attempts"] == 1 and row["note"] == "it needs a prototype"


@pytest.mark.parametrize("task", ["readable", "data", "port"])
def test_a_session_that_isnt_matching_gets_no_function_claimed_for_it(tmp_path, monkeypatch, task):
    """A port-fix helper on a finished game was handed claim_function's "nothing to claim" before it started, so the
    app took it for out of work and never asked the model anything."""
    from decompnet.app import agents, tools
    from tests.test_agents import URL, _no_agent_settings
    from tests.test_agents import _game as agent_game
    _no_agent_settings(monkeypatch)
    net = agent_game(tmp_path)
    manager = agents.Manager(net, tmp_path, URL)

    def claimed(*args, **kwargs):
        raise AssertionError("only a matching session gets a function claimed for it")
    monkeypatch.setattr(tools, "_claim", claimed)
    session = agents.Session("worker", "claude", "worker", "game", "worker", {"task": task})
    manager.prepare_session(session, net.local_agent("worker")[0])
    assert "claim_function" not in session.startup_context


def test_a_long_function_is_briefed_by_its_flagged_lines_and_fixed_with_edits(project):
    net, cc = project
    me = agent(net)
    # a twin's C can be a whole preprocessed file: hundreds of blank and declaration lines before the function
    long = "\n" * 400 + "typedef int s32;\n" + "\n" * 100 + "s32 get_first(s32 table) { return *(s32 *)table; }"
    with net.store.tx() as db:
        db.execute("UPDATE functions SET matched_source=? WHERE name='get_first'", (long,))
    pc_compile.check(net, "game", print, cc=cc)
    brief = port_agents.claim(net, me, "game")
    assert brief.startswith("Claimed get_first") and "Its C is 502 lines (500 of them blank)" in brief
    assert "  502> s32 get_first(s32 table) { return *(s32 *)table; }" in brief and brief.count("\n") < 40
    assert "edits" in brief and "Don't send the whole C back" in brief
    edit = [{"old": "s32 get_first(s32 table) { return *(s32 *)table; }", "new": "s32 get_first(s32 *table) { return *table; }"}]
    assert "0 errors, 0 pointer-size" in port_fix.check(net, "game", "get_first", edits=edit)
    with pytest.raises(NetworkError, match="occurs 0 times|not found|doesn't occur|isn't in"):
        port_fix.fix(net, me, "game", "get_first", edits=[{"old": "no such text", "new": ""}])
    with pytest.raises(NetworkError, match="not both"):
        port_fix.fix(net, me, "game", "get_first", FIXED, edits=edit)
    kept = port_fix.fix(net, me, "game", "get_first", edits=edit)
    assert kept["kept"], kept["text"]
    assert net._function("get_first", "game")["matched_source"] == long.replace(edit[0]["old"], edit[0]["new"])
