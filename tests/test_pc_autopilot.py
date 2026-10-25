"""Autopilot, drafts and flags on Windows PC games (pc-games.md #4): exact drafts for the tiny shapes Visual C++ makes,
Ghidra's drafts made compilable, drafts made in the background and redraft, and the frame pointer kept per source
file (/Oy-), which search_flags tries. The end-to-end part runs on Demo Quest PC where Visual C++ (proprietary),
objdiff and Ghidra are installed."""

import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import consoles
from decompnet.app import tools
from decompnet.consoles.pc import drafts
from decompnet.consoles.pc import scratch as scratch_pe

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_TOOLS = Path(scratch_pe.__file__).with_name("project_template") / "tools"
AT = 0x401000


def _exact(hexcode: str, relocs=(), known: str = "") -> str:
    """The one exact draft of this code at AT, prelude and definition as one text ("" for none)."""
    found = drafts.exact(bytes.fromhex(hexcode), AT, [list(r) for r in relocs], known)
    assert len(found) <= 1
    return "".join(found[0]) if found else ""


def test_a_getter_and_a_setter_are_methods_of_a_class_with_their_field():
    text = _exact("8b4108c3")  # mov eax, [ecx+8]; ret
    assert "struct C_00401000 {\n    char pad0[0x8];\n    int unk8;\n    int func_00401000(void);\n};" in text
    assert "int C_00401000::func_00401000(void) {\n    return unk8;\n}" in text
    # with a frame (/Oy-): push ebp; mov ebp, esp; mov eax, [ebp+8]; mov [ecx+4], eax; pop ebp; ret 4
    text = _exact("558bec8b4508894104" "5dc20400")
    assert "void C_00401000::func_00401000(int a0) {\n    unk4 = a0;\n}" in text
    assert "char pad0[0x4];\n    int unk4;" in text
    text = _exact("d94110c3")  # fld dword ptr [ecx+0x10]; ret: a float field
    assert "float unk10;" in text and "float C_00401000::func_00401000(void) {\n    return unk10;\n}" in text
    text = _exact("8bc1c700" "00204000" "c3", [(4, 6, "_D_00402000", 0)])  # a constructor: the vtable, then this
    assert "C_00401000* C_00401000::func_00401000(void) {\n    extern int D_00402000;\n    unk0 = (int)&D_00402000;\n" \
           "    return this;\n}" in text


def test_constants_globals_and_calls_are_written_as_the_code_makes_them():
    assert "int func_00401000(void) {\n    return 42;\n}" in _exact("b82a000000c3")
    assert "int __stdcall func_00401000(int a0, int a1) {\n    return 0;\n}" in _exact("33c0c20800")
    text = _exact("a100204000c3", [(1, 6, "_D_00402000", 0)])  # mov eax, [D]; ret
    assert "int func_00401000(void) {\n    extern int D_00402000;\n    return D_00402000;\n}" in text
    # a global object's constructor: push 0; push 1; mov ecx, <global>; call <ctor>; ret
    text = _exact("6a006a01b900204000e800000000c3", [(5, 6, "_D_00402000", 0), (10, 20, "@func_00401100@12", 0)])
    assert "struct C_00401000 {\n    void func_00401100(int, int);\n};" in text
    assert "((C_00401000*)&D_00402000)->func_00401100(1, 0);" in text
    # its destructor registered: push <function>; call <atexit>; pop ecx; ret (the caller cleans: __cdecl)
    text = _exact("68" "10114000" "e800000000" "59c3", [(1, 6, "_func_00401110", 0), (6, 20, "_func_00401200", 0)])
    assert "int func_00401110(...);" in text and "int func_00401200(...);" in text
    assert "func_00401200((int)func_00401110);" in text
    text = _exact("6a05e800000000c3", [(3, 20, "_func_00401200@4", 0)])  # the callee cleans up: __stdcall
    assert "int __stdcall func_00401200(int);" in text and "func_00401200(5);" in text
    assert "void func_00401000(void) {\n    int func_00401200(...);\n    func_00401200();\n}" in \
        _exact("e900000000", [(1, 20, "_func_00401200", 0)])  # a thunk
    # a static initializer: fld dword [a]; fmul qword [b]; fstp dword [c]; ret
    text = _exact("d905" "00204000" "dc0d" "08204000" "d91d" "10204000" "c3",
                  [(2, 6, "_D_00402000", 0), (8, 6, "_D_00402008", 0), (14, 6, "_D_00402010", 0)])
    assert "extern float D_00402000;" in text and "extern double D_00402008;" in text
    assert "D_00402010 = D_00402000 * D_00402008;" in text and "extern float D_00402010;" in text
    # what the header or file declares already is used as it is
    assert "extern" not in _exact("a100204000c3", [(1, 6, "_D_00402000", 0)], known="extern long D_00402000;")


def test_code_no_line_of_c_makes_gets_no_exact_draft():
    assert _exact("83e908e900000000", [(4, 20, "_func_00401200", 0)]) == ""  # an adjustor thunk: the compiler's
    assert _exact("8b4108c3" "e800000000c3", [(5, 20, "_func_00401200", 0)]) == ""  # more code after its end
    assert _exact("85c97402" "33c0c3") == ""  # a branch
    assert _exact("8b0485" "00204000" "c3", [(3, 6, "_D_00402000", 0)]) == ""  # an indexed load
    assert _exact("ff2500204000", [(2, 6, "__imp__Sleep", 0)]) == ""  # an import


GHIDRA_METHOD = """
/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

int _func_00401000(void)

{
  int *in_ECX;

  return in_ECX[1] - *in_ECX >> 2;
}
"""
GHIDRA_CALLS = """
void _func_00401000(int param_1)

{
  int iVar1;
  char *pcVar2;

  __D_00402010 = param_1;
  iVar1 = _func_00401200_8(param_1,&_D_00402000);
  pcVar2 = _func_00401300(iVar1);
  return;
}
"""
SYMBOLS = {"func_00401200": "_func_00401200@8", "func_00401300": "_func_00401300"}


def test_ghidras_draft_of_a_method_gets_this_and_a_class():
    prelude, code = drafts.compilable(GHIDRA_METHOD, "_func_00401000", SYMBOLS)
    assert prelude == "struct C_00401000 {\n    int func_00401000(void);\n};\n"
    assert code.startswith('#include "ghidra.h"\nint C_00401000::func_00401000(void)\n{\n')
    assert "  int* in_ECX = (int*)this;\n" in code and "int *in_ECX;" not in code and "WARNING" not in code
    # a class the header declares the method in is used, and nothing is declared again
    prelude, code = drafts.compilable(GHIDRA_METHOD, "_func_00401000", SYMBOLS,
                                      "struct Town {\n    int x;\n    int func_00401000();\n};\n")
    assert prelude == "" and "int Town::func_00401000(void)" in code


def test_ghidras_callees_and_globals_are_declared_by_their_conventions():
    prelude, code = drafts.compilable(GHIDRA_CALLS, "_func_00401000", SYMBOLS)
    assert prelude == "" and "void func_00401000(int param_1)\n{\n" in code
    assert "extern undefined4 D_00402010;" in code and "extern undefined D_00402000;" in code
    assert "D_00402010 = param_1;" in code
    # a __stdcall callee takes ints, as many as it takes off the stack: Ghidra's arguments are cast to them
    assert "int __stdcall func_00401200(int, int);" in code
    assert "func_00401200((int)(param_1), (int)(((undefined *)&D_00402000)))" in code
    # a __cdecl one takes anything, and what it returns is cast to the pointer it's kept in
    assert "int func_00401300(...);" in code and "pcVar2 = (char *)func_00401300(iVar1);" in code
    assert drafts.compilable(GHIDRA_CALLS, "_func_00401000@4", SYMBOLS) is not None  # __stdcall, one argument
    assert drafts.compilable(GHIDRA_CALLS, "_func_00401000@8", SYMBOLS) is None  # Ghidra read another count
    assert drafts.compilable(GHIDRA_CALLS.replace("_func_00401200_8(param_1,", "_func_00401200_8("), "_func_00401000",
                             SYMBOLS) is None  # it passes another number of arguments than the callee takes
    assert drafts.compilable(GHIDRA_CALLS.replace("param_1;", "unaff_ESI;"), "_func_00401000", SYMBOLS) is None


def _tiny_pe(code: bytes) -> scratch_pe.PE:
    return scratch_pe.PE(b"\0" * 0x400 + code, 0x400000, 0x2000, AT, 0,
                         [scratch_pe.Section(".text", AT, len(code), 0x400, len(code), True)])


FRAMED = bytes.fromhex("558bec8b4508") + b"\x90" * 26 + bytes.fromhex("5dc3")  # 34 bytes, keeps a frame
FRAMELESS = bytes.fromhex("8b442404") + b"\x90" * 30  # 34 bytes, doesn't
HANDLER = bytes.fromhex("558bec6aff680010400064a100000000") + b"\x90" * 18  # a frame an exception handler needs
TINY = bytes.fromhex("558bec5dc3")  # too small to say


def test_files_whose_functions_keep_a_frame_build_with_oy_minus():
    parts = {"a": [FRAMED, FRAMED, FRAMELESS], "b": [FRAMELESS, FRAMELESS, HANDLER, HANDLER], "c": [TINY],
             "d": [FRAMELESS, FRAMELESS, FRAMELESS]}
    code, groups = b"", {}
    for unit, fns in parts.items():
        for body in fns:
            groups.setdefault(unit, []).append(scratch_pe.Function(AT + len(code), len(body), f"_f{len(code)}",
                                                                   unit=unit))
            code += body
    pe = _tiny_pe(code)
    assert scratch_pe.keeps_frames(pe, groups["a"]) and not scratch_pe.keeps_frames(pe, groups["b"])
    assert scratch_pe.keeps_frames(pe, groups["c"]) is None
    # most of the program doesn't keep one: a file that doesn't say follows that
    assert scratch_pe.frame_flags(pe, groups) == {"src/auto/a.cpp": ["/Oy-"]}
    framed = {u: [scratch_pe.Function(f.addr, f.size, f.name, unit=u) for f in fns] for u, fns in groups.items()
              if u != "d"}
    framed["e"] = [scratch_pe.Function(groups["a"][0].addr, len(FRAMED), "_x", unit="e")] * 3
    assert scratch_pe.frame_flags(pe, framed) == {"src/auto/a.cpp": ["/Oy-"], "src/auto/c.cpp": ["/Oy-"],
                                                  "src/auto/e.cpp": ["/Oy-"]}


def test_pc_search_flags_tries_the_frame_pointer_size_and_inlining():
    pc = consoles.get("pc")
    assert pc.flag_variants([]) == [[], ["/Oy-"], ["/O1"], ["/Ob1"], ["/O1", "/Oy-"]]
    assert pc.flag_variants(["/Oy-"])[:2] == [["/Oy-"], []]  # the file's own first, then without it
    assert consoles.get("gc").flag_variants(["-O4"]) == [["-O4"]] and consoles.get("gc").file_flags(None, "x") is None


def test_pc_games_started_from_scratch_have_redraft_and_search_flags():
    assert {"redraft", "search_flags"} <= set(tools.tool_names(True, consoles.get("pc")))
    assert not {"redraft", "search_flags"} & set(tools.tool_names(True))  # a public decomp's
    assert not {"redraft", "search_flags"} & set(tools.tool_names(True, consoles.get("gc")))


def test_a_file_with_flags_of_its_own_is_built_with_them(tmp_path, monkeypatch):
    """configure.py gives each source file the flags file_flags.json names (on top of CL_FLAGS), and a change to that
    file makes ninja write the build again."""
    import shutil

    root = tmp_path / "p"
    shutil.copytree(TEMPLATE_TOOLS, root / "tools")
    for name in ("a", "b"):
        (root / "src" / "auto").mkdir(parents=True, exist_ok=True)
        (root / "src" / "auto" / f"{name}.cpp").write_text("int x;\n")
    (root / "include").mkdir()
    conf = root / "config" / "T"
    conf.mkdir(parents=True)
    (conf / "functions.json").write_text(json.dumps({"exe": "orig/T.exe", "units": {"a": [], "b": []}}))
    scratch_pe.set_file_flags(root, "T", "src/auto/a.cpp", ["/Oy-"])
    msvc = tmp_path / "msvc" / "6.0"
    (msvc / "Bin").mkdir(parents=True)
    (msvc / "Bin" / "CL.EXE").write_text("")
    (msvc / "Include").mkdir()
    subprocess.run([sys.executable, "tools/configure.py", "T", "--msvc", str(tmp_path / "msvc")], cwd=root, check=True,
                   capture_output=True)
    ninja = (root / "build.ninja").read_text()
    block = lambda unit: ninja.split(f"build build/T/src/auto/{unit}.obj: cl", 1)[1].split("\nbuild ", 1)[0]  # noqa: E731
    assert "cflags = /Oy-" in block("a") and "cflags = /Oy-" not in block("b")
    assert "config/T/file_flags.json" in ninja.split("build build.ninja: configure", 1)[1].split("\n\n", 1)[0]
    scratch_pe.set_file_flags(root, "T", "src/auto/a.cpp", [])
    assert scratch_pe.file_flags(root, "T") == {}


# ---------------------------------------------------------------------------------------------------- end to end

from .test_pc import DEMO, EXE, _addresses, _tiny_exe, needs_tools  # noqa: E402

needs_ghidra = pytest.mark.skipif(not __import__("decompnet.forks.ghidra", fromlist=["x"]).available(),
                                  reason="needs Ghidra")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    from decompnet.app.engine import RomNetwork

    if not EXE.exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    work = tmp_path_factory.mktemp("pcauto")
    net = RomNetwork(work / "app.db", work)
    slug = net.import_rom(EXE, confirmed=True, log=lambda *_: None)
    return net, slug


@needs_tools
@needs_ghidra
def test_autopilot_matches_the_demos_functions_from_ghidras_drafts_made_compilable(demo):
    """Autopilot on a PC game: what it tries compiles (Ghidra's drafts didn't, as they were), and the small functions
    whose drafts are right match, each judged like an agent's attempt."""
    net, slug = demo
    agent, _ = net.local_agent("autopilot")
    proj, fp, _ = net.forks.get(slug)
    ids = net.forks.autopilot_prepare(slug)
    assert (fp.root / "include" / "ghidra.h").exists()
    outcomes = {net.store.one("SELECT name FROM functions WHERE id=?", fid)["name"]: net.forks.autopilot_one(
        agent, proj, fid) for fid in ids}
    rename = {v: k for k, v in _addresses().items()}
    matched = {rename.get(n.lstrip("_@").split("@")[0], n) for n, o in outcomes.items() if o == "match"}
    assert {"abs_s32", "rng_next", "lerp", "entity_points"} <= matched, outcomes  # (a jump table too)
    assert sum(o != "fail" for o in outcomes.values()) >= len(outcomes) // 2, outcomes  # most compile
    # tried as they are: the same drafts aren't judged again
    fid = next(fid for fid in ids if net.store.one("SELECT status FROM functions WHERE id=?", fid)["status"] == "open"
               and net.store.one("SELECT 1 FROM attempts WHERE function_id=?", fid))
    assert net.forks.autopilot_one(agent, proj, fid) == "skip"


@needs_tools
@needs_ghidra
def test_pc_drafts_are_made_in_the_background_and_follow_the_headers(demo):
    net, slug = demo
    proj = net.project_row(slug)
    assert net.can_draft(proj)
    mark = net.draft_mark(proj)
    for fid in net.stale_drafts(proj):
        net.forks.background_draft(proj, net.store.one("SELECT * FROM functions WHERE id=?", fid))
    assert not [fid for fid in net.stale_drafts(proj) if not net.store.one(
        "SELECT 1 FROM attempts WHERE function_id=?", fid)]  # every draft made; the rest are autopilot's retries
    agent, _ = net.local_agent("pc-agent")
    _, fp, cfg = net.forks.get(slug)
    fn = next(r for r in net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY n_insns",
                                       proj["id"]) if net.forks._compilable(proj, fp, cfg, r, fp.unit(r["unit"])))
    with net.store.tx() as db:  # (autopilot left it its best attempt: the brief shows the draft without one)
        db.execute("UPDATE functions SET best_source=NULL, best_score=NULL WHERE id=?", (fn["id"],))
    brief = net.forks.get_function(agent, slug, fn["name"])
    assert "Machine draft (Ghidra's, made to compile here" in brief and '#include "ghidra.h"' in brief
    name = fn["name"].lstrip("_@").split("@")[0]
    net.forks.edit_file(agent, slug, "include/hero.h", "", f"struct Hero {{ int {name}(int); }};\n", "a class")
    assert net.draft_mark(proj) != mark  # the drafter looks again, and autopilot tries what changed
    redrafted = net.redraft(slug, fn["name"], f"struct Hero {{ int {name}(int); }};")
    assert "Recorded nowhere" in redrafted and "```cpp" in redrafted


@needs_tools
def test_search_flags_finds_the_flags_a_file_was_built_with(demo):
    """A file built with /Oy- by mistake: C that matched without it doesn't now, and search_flags finds the
    variant it matches with and keeps it; with the right flags it says there's nothing to change."""
    net, slug = demo
    agent, _ = net.local_agent("pc-agent")
    _, fp, _ = net.forks.get(slug)
    pc = consoles.get("pc")
    rename = _addresses()
    fn = net._function(next(n for n in (r["name"] for r in net.store.all(
        "SELECT name FROM functions WHERE project_id=?", net.project_row(slug)["id"])) if rename["settings_get"] in n),
        slug)
    code = (f"extern int {rename['g_settings']}[6];\nint __stdcall {rename['settings_get']}(int index, int fallback) "
            f"{{\n    if (index < 0 || index >= 6) {{\n        return fallback;\n    }}\n    return "
            f"{rename['g_settings']}[index];\n}}\n")
    source = fp.unit(fn["unit"]).source
    pc.set_file_flags(fp, source, ["/Oy-"])
    if fn["status"] != "matched":
        res = net.forks.attempt(agent, slug, fn["name"], code, keep=False)
        assert not res["matched"]  # it reads its arguments: with /Oy- it keeps a frame the game's doesn't
        said = net.search_flags(agent, slug, fn["name"], source=code)
        assert "is built with them from now on" in said and "(the project's flags only)" in said, said
        assert pc.file_flags(fp, source) == []
        fn = net._function(fn["name"], slug)
        assert fn["status"] == "matched"
    with pytest.raises(Exception, match="already matches"):
        net.search_flags(agent, slug, fn["name"], source=code)


def test_on_start_reads_frames_only_for_files_without_matched_code(tmp_path):
    root = tmp_path
    code = FRAMED * 3 + FRAMED * 3
    exe = root / "orig" / "game.exe"
    exe.parent.mkdir()
    exe.write_bytes(_tiny_exe(code))
    units = {"a": [{"name": f"_func_{AT + k * len(FRAMED):08X}", "addr": AT + k * len(FRAMED),
                    "offset": 0x200 + k * len(FRAMED), "size": len(FRAMED), "relocs": []} for k in range(3)],
             "b": [{"name": f"_func_{AT + k * len(FRAMED):08X}", "addr": AT + k * len(FRAMED),
                    "offset": 0x200 + k * len(FRAMED), "size": len(FRAMED), "relocs": []} for k in range(3, 6)]}
    (root / "config" / "g").mkdir(parents=True)
    (root / "config" / "g" / "functions.json").write_text(json.dumps({"exe": "orig/game.exe", "units": units}))
    (root / "src" / "auto").mkdir(parents=True)
    for u in units:
        (root / "src" / "auto" / f"{u}.cpp").write_text("")
    assert scratch_pe.set_frame_flags(root, "g", keep={"src/auto/b.cpp"}) == 1
    assert scratch_pe.file_flags(root, "g") == {"src/auto/a.cpp": ["/Oy-"]}
    assert scratch_pe.set_frame_flags(root, "g") == 1  # b now; a keeps what it has
    exe.unlink()
    assert scratch_pe.set_frame_flags(root, "g") is None  # without the program: next start


def test_the_ghidra_header_is_what_drafts_include():
    assert "typedef unsigned int undefined4;" in drafts.GHIDRA_H and "#define CONCAT44" in drafts.GHIDRA_H
    assert struct.calcsize("<I") == 4
