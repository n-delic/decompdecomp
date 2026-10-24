"""PC (Windows) from scratch: a project generated for the user's .exe from Ghidra's analysis, worked on like a fork.

Demo Quest PC (projects/demo-pc) is built like a PC game of its time: Visual C++ 6.0 /O2, linked at 0x400000 without
relocations, with __stdcall and __fastcall functions, a jump table and a hand-written assembly helper. Importing it
must find every function with its exact extent, and agents' C must be judged by the project's own build and objdiff.
Visual C++ is proprietary, so the end-to-end part runs only where `msvc` was installed with its license accepted.
"""

import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import consoles, toolchains
from decompnet.consoles.pc import scratch as scratch_pe
from decompnet.forks import csource, ghidra
from decompnet.forks.project import ForkError, ForkProject

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "projects" / "demo-pc"
EXE, MAP = DEMO / "build" / "demoquest.exe", DEMO / "build" / "demoquest.map"
GAME = ROOT / "projects" / "demo-n64" / "original" / "game.c"

needs_tools = pytest.mark.skipif(
    any(toolchains.installed(t) is None for t in ("msvc", "objdiff-cli")) or not ghidra.available()
    or (sys.platform != "win32" and toolchains.get("wibo").supported and toolchains.installed("wibo") is None),
    reason="needs Visual C++ (msvc, proprietary), Ghidra and objdiff")


def test_decorated_names():
    assert scratch_pe.decorate("func_00401000", "__cdecl", 0, 2) == "_func_00401000"
    assert scratch_pe.decorate("func_00401000", "__stdcall", 0, 0) == "_func_00401000"  # Ghidra's default: nothing popped
    assert scratch_pe.decorate("func_00401000", "__stdcall", 8, 2) == "_func_00401000@8"
    assert scratch_pe.decorate("func_00401000", "__fastcall", 0, 2) == "@func_00401000@8"
    assert scratch_pe.decorate("func_00401000", "__fastcall", 4, 3) == "@func_00401000@12"
    assert scratch_pe.decorate("func_00401000", "__thiscall", 4, 2) == "@func_00401000@12"  # this, unused edx, one int


def test_generated_pc_builds_quote_paths_and_only_process_successful_objects(tmp_path, monkeypatch):
    """The compiler and its includes may live in a Windows user folder with spaces or shell characters."""
    root = tmp_path / "Project with spaces ü"
    tools = Path(scratch_pe.__file__).with_name("project_template") / "tools"
    shutil.copytree(tools, root / "tools")
    source = root / "src/auto/sample.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int sample(void) { return 1; }\n")
    config = root / "config/TEST/functions.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"units": {"sample": {}}}))
    (root / "include").mkdir()
    compiler_root = tmp_path / "Compiler & tools $ ü"
    compiler = compiler_root / "6.0/Bin/CL.EXE"
    compiler.parent.mkdir(parents=True)
    (compiler_root / "6.0/Include").mkdir()
    record = root / "compiler-args.txt"
    monkeypatch.setenv("DECOMP_TEST_CL_ARGS", str(record))
    monkeypatch.delenv("DECOMP_TEST_CL_FAIL", raising=False)
    if sys.platform == "win32":
        subprocess.run([sys.executable, str(Path(__file__).with_name("fake_clr_compiler.py")), str(compiler)],
                       check=True, capture_output=True, timeout=30)
    else:
        compiler.write_text('#!/usr/bin/env python3\nimport os, sys\nfrom pathlib import Path\n'
                            'Path(os.environ["DECOMP_TEST_CL_ARGS"]).write_text("\\n".join(sys.argv[1:]))\n'
                            'if os.environ.get("DECOMP_TEST_CL_FAIL") == "1": sys.exit(3)\n'
                            'out = Path(next(arg[3:] for arg in sys.argv if arg.startswith("/Fo")))\n'
                            'out.parent.mkdir(parents=True, exist_ok=True)\nout.write_text("object")\n')
        compiler.chmod(0o755)
    # Only command routing is under test: no fake object ever reaches the real judge.
    (root / "tools/names.py").write_text('import sys\nfrom pathlib import Path\n'
                                        'out = Path(sys.argv[2])\nassert out.read_text() == "object"\n'
                                        'out.with_suffix(".named").write_text("processed")\n')
    subprocess.run([sys.executable, str(root / "tools/configure.py"), "TEST", "--msvc", str(compiler_root)],
                   check=True, capture_output=True, timeout=30)
    project = ForkProject(root)
    ok, output = project.run(["ninja", "all_source"], log=lambda _: None, timeout=30)
    assert ok, output
    obj = root / "build/TEST/src/auto/sample.obj"
    assert obj.with_suffix(".named").read_text() == "processed"
    args = record.read_text(encoding="utf-8-sig").splitlines()
    assert "/I" + str(compiler_root / "6.0/Include") in args and "/GX" in args
    obj.with_suffix(".named").unlink()
    obj.unlink()
    source.write_text("int sample(void) { return 2; }\n")  # also exercise configure.py's regeneration command
    monkeypatch.setenv("DECOMP_TEST_CL_FAIL", "1")
    ok, output = project.run(["ninja", "all_source"], log=lambda _: None, timeout=30)
    assert not ok and not obj.with_suffix(".named").exists(), output


def _template_tool(name: str):
    import importlib.util

    path = Path(scratch_pe.__file__).with_name("project_template") / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"pc_template_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_real_name_stands_for_its_address_however_c_or_cpp_spells_it():
    names = _template_tool("names")
    real = names.real_names({"names": {"func_00401000": "UpdateTown", "D_00405000": "g_town"}})
    for sym in (b"?UpdateTown@@YAXH@Z", b"?UpdateTown@Town@@QAEXH@Z", b"_UpdateTown", b"_UpdateTown@8", b"@UpdateTown@8"):
        assert names.address_of(sym, real) == b"func_00401000", sym
    assert names.address_of(b"?g_town@@3HA", real) == names.address_of(b"_g_town", real) == b"D_00405000"
    assert names.address_of(b"_func_00402000@4", real) == b"func_00402000"  # an address still says it
    for other in (b"?UpdateTownHall@@YAXXZ", b"_memcpy", b"??0Town@@QAE@XZ", b".text"):
        assert names.address_of(other, real) is None, other


def test_a_pc_symbol_is_renamed_by_its_address_in_functions_json(tmp_path):
    from decompnet.forks.project import ForkError

    conf = tmp_path / "config" / "v"
    conf.mkdir(parents=True)
    (conf / "functions.json").write_text(json.dumps(
        {"units": {"a": [{"name": "_func_00401000@8", "addr": 0x401000, "relocs": []}]}}, indent=1))
    fp, pc = ForkProject(tmp_path), consoles.get("pc")
    assert list(pc.rename_symbol(fp, "v", "_func_00401000@8", "UpdateTown")) == [conf / "functions.json"]
    assert pc.real_names(fp, "v") == {"_func_00401000@8": "UpdateTown"}  # by the report's name
    pc.rename_symbol(fp, "v", "UpdateTown", "Town_Update")  # again, by the name it has
    pc.rename_symbol(fp, "v", "D_00405000", "g_town")
    assert json.loads((conf / "functions.json").read_text())["names"] == {"D_00405000": "g_town",
                                                                         "func_00401000": "Town_Update"}
    for old, new in (("func_00409999", "Nowhere"), ("nothing", "Other"), ("D_00405004", "g_town")):
        with pytest.raises(ForkError):
            pc.rename_symbol(fp, "v", old, new)
    # however the source spells it: the report's decorated name, a method, not a longer address
    pattern = pc.rename_pattern("_func_00401000@8")
    assert re.sub(pattern, "T", "void Town::func_00401000(int); _func_00401000(); func_004010001();") == \
        "void Town::T(int); T(); func_004010001();"


def test_two_renames_at_once_both_reach_every_file(tmp_path, monkeypatch):
    """Eight polishing helpers renamed at once: each read the files before taking the project's lock and wrote them
    back after, undoing the renames made meanwhile in some files and not others, and an Xbox game's harness link lost
    six functions. Here both start while something else (a build) holds the project."""
    import threading

    from decompnet.app.engine import RomNetwork
    from decompnet.forks.project import Unit

    net = RomNetwork(tmp_path / "decomp.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    (root / "config" / "v").mkdir(parents=True)
    (root / "config" / "v" / "functions.json").write_text(json.dumps({"units": {"u": [
        {"name": "_func_00401000", "addr": 0x401000, "relocs": []},
        {"name": "_func_00401010", "addr": 0x401010, "relocs": []}]}}, indent=1))
    (root / "src" / "auto").mkdir(parents=True)
    source = root / "src" / "auto" / "u.cpp"
    source.write_text("int func_00401000(int x) { return x; }\nint func_00401010(int x) { return func_00401000(x); }\n")
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "origin": "scratch", "version": "v",
                                                  "platform": "pc", "ready": True}, "x")
    report = {"units": [{"name": "u", "functions": [{"name": n, "size": "8", "fuzzy_match_percent": 100.0}
                                                    for n in ("_func_00401000", "_func_00401010")]}]}
    monkeypatch.setattr(ForkProject, "report", lambda self: report)
    monkeypatch.setattr(ForkProject, "units", lambda self: [Unit("u", None, None, "src/auto/u.cpp", False, None)])
    monkeypatch.setattr(ForkProject, "setup_commands", lambda self, version: [])
    monkeypatch.setattr(ForkProject, "git", lambda self, *args: "")
    net.forks.sync("p", log=lambda *_: None)
    agent, _ = net.local_agent("claude-1")
    said = {}
    first = threading.Thread(target=lambda: said.update(a=net.rename_symbol(agent, "p", "_func_00401000", "Identity")))
    second = threading.Thread(target=lambda: said.update(b=net.rename_symbol(agent, "p", "_func_00401010", "Pass")))
    with net.forks._tree(net.project_row("p")).exclusive():  # a build holds the project while both start
        first.start()
        second.start()
        first.join(0.5)  # (they wait for it now; before, each read the files here)
    first.join(10)
    second.join(10)
    assert said["a"].startswith("✅") and said["b"].startswith("✅"), said
    assert source.read_text() == "int Identity(int x) { return x; }\nint Pass(int x) { return Identity(x); }\n"
    names = json.loads((root / "config" / "v" / "functions.json").read_text())["names"]
    assert names == {"func_00401000": "Identity", "func_00401010": "Pass"}


def test_only_32_bit_x86_programs():
    assert not scratch_pe.is_pe(b"MZ" + bytes(0x3E))
    head = bytearray(0x200)
    head[:2], head[0x3C:0x40], head[0x80:0x84] = b"MZ", struct.pack("<I", 0x80), b"PE\0\0"
    struct.pack_into("<H", head, 0x84, 0x14C)
    struct.pack_into("<H", head, 0x98, 0x10B)
    assert scratch_pe.is_pe(bytes(head))
    struct.pack_into("<H", head, 0x84, 0x8664)  # x64
    assert not scratch_pe.is_pe(bytes(head))


def test_a_function_your_object_only_calls_is_not_in_your_build_not_a_match(monkeypatch):
    import json

    from decompnet.forks import objdiff

    # an object that calls F but doesn't define it (a unit whose definition went missing): objdiff lists the call's
    # undefined symbol under the function's name, with no size or code. The function's diff read 100% (its code
    # against nothing), the full report said not built, and every full measure reopened a function the next
    # compile "matched" again (63 times in 14 hours in one project)
    ins = [{"instruction": {"formatted": "mov eax, 1", "size": 5}}, {"instruction": {"formatted": "ret", "size": 1}}]
    left = {"symbols": [{"name": "?F@@YA_NXZ", "kind": "SYMBOL_FUNCTION", "size": "6", "instructions": ins}]}
    right = {"symbols": [{"name": "?F@@YA_NXZ", "kind": "SYMBOL_FUNCTION", "flags": {"global": True}},
                         {"name": "?G@@YAXXZ", "kind": "SYMBOL_FUNCTION", "size": "6", "instructions": ins}]}
    monkeypatch.setattr(objdiff, "_run", lambda *a, **k: json.dumps({"left": left, "right": right}))
    d = objdiff.diff(None, None, "?F@@YA_NXZ", cwd=Path("."))
    assert d.match_percent is None and not d.matched
    assert d.unit_scores["?F@@YA_NXZ"] is None
    # defined after its undefined copy: that's the one compared
    right["symbols"].append({"name": "?F@@YA_NXZ", "kind": "SYMBOL_FUNCTION", "size": "6", "match_percent": 100.0,
                             "instructions": ins})
    d = objdiff.diff(None, None, "?F@@YA_NXZ", cwd=Path("."))
    assert d.match_percent == 100.0 and d.matched


def _map() -> dict[str, int]:
    """Symbol -> address of every function and variable, from the linker's map of the demo."""
    return {m.group(1): int(m.group(2), 16)
            for m in re.finditer(r"^\s+\S+\s+(\S+)\s+([0-9a-f]{8}) ", MAP.read_text(), re.M)}


@pytest.fixture(scope="module")
def pc(tmp_path_factory):
    from decompnet.app.engine import RomNetwork

    if not EXE.exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    work = tmp_path_factory.mktemp("pc")
    net = RomNetwork(work / "app.db", work)
    slug = net.import_rom(EXE, confirmed=True, log=lambda *_: None)
    return net, slug


@needs_tools
def test_every_function_is_found_with_its_extent(pc):
    net, slug = pc
    ov = net.overview(slug)
    assert ov["kind"] == "fork" and ov["origin"] == "scratch" and ov["platform"] == "pc" and ov["ready"]
    assert ov["rebuild"]["identical"]  # its code, put back together from the delinked functions, is the program's
    syms = {a: s for s, a in _map().items() if not s.startswith("??") and a < 0x402000}
    rows = net.store.all("SELECT name, size FROM functions WHERE project_id=?", net.project_row(slug)["id"])
    assert len(rows) == len(syms) == 19  # clamp_s32, which nothing calls, too: Ghidra misses it, the gap doesn't
    for name, size in ((r["name"], r["size"]) for r in rows):
        addr = int(re.search(r"func_([0-9A-F]{8})", name).group(1), 16)
        assert addr in syms
        assert name.startswith("@") == syms[addr].startswith("@") and name.endswith("@8") == syms[addr].endswith("@8")
    root = Path(net.project_row(slug)["root"])
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    assert files and not [f for f in files if f.startswith(("orig/", "build/"))]
    assert "config/demoquest/functions.json" in files and any(f.startswith("src/auto/") and f.endswith(".cpp") for f in files)


@needs_tools
def test_agents_match_functions(pc):
    net, slug = pc
    syms = _map()
    agent, _ = net.local_agent("pc-agent")
    header = (GAME.parent / "game.h").read_text().split("#define NULL ((void *)0)", 1)[1].rsplit("#endif", 1)[0]
    rename = {re.sub(r"^[_@]|@\d+$", "", s): (f"func_{a:08X}" if a < 0x402000 else f"D_{a:08X}")
              for s, a in syms.items() if not s.startswith("??") and s != "__fltused"}
    pattern = re.compile(r"\b(" + "|".join(sorted(rename, key=len, reverse=True)) + r")\b")
    sub = lambda text: pattern.sub(lambda m: rename[m.group(1)], text)
    header += "extern s32 g_settings[6];\n"
    assert net.forks.edit_file(agent, slug, "include/common.h", "", sub(header), "the game's types").startswith("✅")
    source = GAME.read_text() + (DEMO / "pcmain.c").read_text()
    for name in ("clamp_s32", "player_take_damage", "entity_default_hp", "settings_get", "mix_score"):
        decorated = next(s for s in syms if re.sub(r"^[_@]|@\d+$", "", s) == name)
        res = net.attempt(agent, decorated.replace(name, rename[name]), sub(csource.get(source, name)), slug)
        assert res["matched"], res["text"]
    # C++: a class's method, whatever MSVC mangles its name to, pairs with the original's function
    abs_name = rename["abs_s32"]
    assert net.forks.edit_file(agent, slug, "include/common.h", "", f"struct Num {{ static s32 {abs_name}(s32 v); }};\n",
                               "a class").startswith("✅")
    res = net.attempt(agent, f"_{abs_name}", f"s32 Num::{abs_name}(s32 v) {{\n    return v < 0 ? -v : v;\n}}\n", slug)
    assert res["matched"], res["text"]
    # polishing: the comment saying what a function does goes into its file with it (an Xbox game's 18 polished
    # functions kept their definitions only), a new one replaces it, and a rewrite without one leaves it
    root = Path(net.project_row(slug)["root"])
    clamp = next(s for s in syms if re.sub(r"^[_@]|@\d+$", "", s) == "clamp_s32").replace("clamp_s32", rename["clamp_s32"])
    code = sub(csource.get(source, "clamp_s32")).strip() + "\n"
    file = next(p for p in (root / "src").rglob("*.c*") if csource.find(p.read_text(), rename["clamp_s32"]))

    def polish(comment: str) -> str:
        res = net.readability.submit(agent, slug, clamp, comment + code)
        assert "readable now" in res, res
        return file.read_text()

    text = polish("// Keeps v within lo and hi.\n")
    assert "// Keeps v within lo and hi.\n" + code.splitlines()[0] in text  # right above the definition
    text = polish("/* Clamps v to [lo, hi],\n   both ends included. */\n")
    assert "Keeps v within" not in text and text.count("both ends included. */\n" + code.splitlines()[0]) == 1
    code = code.replace("{", "{ /* the bounds are included */", 1)  # (other C than it has: a rewrite)
    text = polish("")
    assert "both ends included. */\n" + code.splitlines()[0] in text  # no comment sent: the file's stays
    # only polishing's comments go in: a twin's note or an agent's scratch comment replaced a forked decomp's own
    res = net.attempt(agent, clamp, "// scratch\n" + code.replace("the bounds", "bounds"), slug)
    assert res["matched"], res["text"]
    text = file.read_text()
    assert "scratch" not in text and "both ends included. */\n" in text
    # real names: a __stdcall function and the global it reads renamed, both still match (an Xbox game's polishing
    # helpers were refused 31 renames); its row keeps the report's name and finds its code by the new one
    getter = next(s for s in syms if re.sub(r"^[_@]|@\d+$", "", s) == "settings_get")
    getter = getter.replace("settings_get", rename["settings_get"])
    assert net.rename_symbol(agent, slug, rename["g_settings"], "g_settings").startswith("✅")
    assert net.rename_symbol(agent, slug, getter, "settings_get").startswith("✅")
    texts = "".join(p.read_text() for p in (root / "src").rglob("*.c*")) + (root / "include" / "common.h").read_text()
    assert "settings_get(" in texts and rename["settings_get"] not in texts and rename["g_settings"] not in texts
    fn = net._function(getter, slug)
    assert fn["status"] == "matched" and json.loads(fn["meta"])["real"] == "settings_get"
    assert net._function("settings_get", slug)["id"] == fn["id"]
    # a renamed global is read by its new name (a helper that named one could no longer read_data it)
    assert net.forks.read_data(agent, slug, "g_settings", 24).startswith(f"g_settings = {rename['g_settings']} (")
    again = sub(csource.get(source, "settings_get")).replace(rename["settings_get"], "settings_get")
    res = net.attempt(agent, "settings_get", again.replace(rename["g_settings"], "g_settings"), slug)
    assert res["matched"], res["text"]
    other = f"_func_{syms['_entity_points']:08X}"
    wrong = net.attempt(agent, other, sub(csource.get(source, "entity_points")).replace("return 75;", "return 76;"),
                        slug)
    assert not wrong["matched"]
    net.release(agent, other, "", slug)
    root = Path(net.project_row(slug)["root"])
    assert "Match " in subprocess.run(["git", "log", "--oneline"], cwd=root, capture_output=True, text=True).stdout


def test_a_program_built_with_a_newer_visual_cpp_is_told_apart():
    """GitHub #12: a 2010 game said only "Ghidra found no functions". Its Rich header could have said why."""
    vc6_game = [(0x0A, 8168, 120), (0x0B, 8168, 30), (0x01, 0, 200)]
    vs2008_game = [(0x83, 30729, 300), (0x84, 30729, 40), (0x91, 30729, 1)]
    vc71_with_a_newer_library = [(0x5F, 3077, 90), (0x6D, 50727, 2)]
    assert scratch_pe.newer_tools(vs2008_game)
    assert not scratch_pe.newer_tools(vc6_game)
    assert not scratch_pe.newer_tools(vc71_with_a_newer_library)  # the game's own compiler is in the pack
    assert not scratch_pe.newer_tools([])  # no Rich header: nothing to go by


# Hand-made x86 code and programs (pieces of functions, the rebuild check): no compiler needed.
PUSH_FRAME = bytes.fromhex("558bec")  # push ebp; mov ebp, esp
CALL_0 = bytes.fromhex("e800000000")  # call (a function Ghidra took for one that never returns: throw)
EH_EPILOGUE = bytes.fromhex("8b4df45f8be55dc3")  # mov ecx, [ebp-0xc]; pop edi; mov esp, ebp; pop ebp; ret
FRAME_RET = PUSH_FRAME + bytes.fromhex("33c08be55dc3")  # ...; xor eax, eax; mov esp, ebp; pop ebp; ret
CATCH = bytes.fromhex("8b45ecc3")  # mov eax, [ebp-0x14]; ret: the frame's locals, after the function's return
UNWIND = bytes.fromhex("8d4df0e900000000")  # lea ecx, [ebp-0x10]; jmp <destructor>: a .text$x funclet


def test_a_piece_ghidra_cut_off_is_the_rest_of_the_function_before_it():
    from decompnet.arch import x86

    # a lone epilogue after a call Ghidra took for one that never returns: the code before runs on into it
    assert "runs on" in x86.piece(PUSH_FRAME + bytes.fromhex("6a00") + CALL_0, EH_EPILOGUE)
    assert "restores edi" in x86.piece(PUSH_FRAME + CALL_0, bytes.fromhex("5f5ec3"))  # pop edi; pop esi; ret
    # a catch block after the return of a function with an ebp frame
    assert "frame" in x86.piece(FRAME_RET, CATCH)
    # without a frame, a short jump from the code before says it continues there (push ebx; mov ebx, ecx; test eax,
    # eax; je +2; pop ebx; ret), then mov eax, [ebx+0x14]; pop ebx; ret
    assert "short jump" in x86.piece(bytes.fromhex("538bd985c074025bc3"), bytes.fromhex("8b43145bc3"))
    assert x86.piece(bytes.fromhex("538bd985c074005bc3"), bytes.fromhex("8b43145bc3")) is None  # (jumps within)


def test_whole_functions_and_exception_funclets_are_not_pieces():
    from decompnet.arch import x86

    assert x86.piece(FRAME_RET + b"\xcc\xcc", CATCH) is None  # padding ends a function
    assert x86.piece(FRAME_RET, PUSH_FRAME + bytes.fromhex("8b4508c3")) is None  # sets its frame before using it
    # MSVC's .text$x funclets use their parent's ebp without a frame, by design: each follows another, which ends
    # with a jmp and sets up no frame (the exception-handling stubs are set aside by their own rule)
    assert x86.piece(UNWIND, UNWIND) is None
    assert x86.piece(bytes.fromhex("b878563412e900000000"), UNWIND) is None  # mov eax, <FuncInfo>; jmp handler
    # a function setting its frame through __EH_prolog (mov eax, <handler>; call), or with enter
    assert x86.piece(FRAME_RET, bytes.fromhex("b878563412") + CALL_0 + bytes.fromhex("8b4508c3")) is None
    assert x86.piece(PUSH_FRAME + CALL_0, bytes.fromhex("c8800000608b450cc3")) is None
    # xor esi, esi only sets esi; a function saving what it restores is whole
    assert x86.piece(PUSH_FRAME + CALL_0, bytes.fromhex("33f68bc6c3")) is None
    assert x86.piece(PUSH_FRAME + CALL_0, bytes.fromhex("56be010000005ec3")) is None
    assert x86.piece(PUSH_FRAME + CALL_0 + b"\x8b", EH_EPILOGUE) is None  # code before ends mid-instruction: data
    # MSVC's alignment filler after a hand-written function (lea esp, [esp]; mov edi, edi) does nothing
    assert x86.piece(bytes.fromhex("578b7c2408eb6a"), bytes.fromhex("8da424000000008bff")) is None


TEXT = 0x401000


def _tiny_exe(code: bytes) -> bytes:
    """A 32-bit Windows program whose .text (at 0x401000) is `code`: just what read_pe and delink.py read."""
    pe_at, opt_bytes, raw_at = 0x40, 0xE0, 0x200
    head = bytearray(raw_at)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe_at)
    head[pe_at:pe_at + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, pe_at + 4, 0x14C, 1, 0, 0, 0, opt_bytes, 0x102)
    opt = pe_at + 24
    struct.pack_into("<H", head, opt, 0x10B)
    struct.pack_into("<I", head, opt + 16, TEXT - 0x400000)  # entry
    struct.pack_into("<I", head, opt + 28, 0x400000)  # image base
    struct.pack_into("<I", head, opt + 56, 0x2000)  # image size
    struct.pack_into("<8sIIIIIIHHI", head, opt + opt_bytes, b".text", len(code), TEXT - 0x400000, len(code), raw_at,
                     0, 0, 0, 0, 0x60000020)
    return bytes(head) + code


def _row(start: int, end: int) -> dict:
    return {"entry": start, "end": end, "name": f"FUN_{start:08x}", "source": "DEFAULT", "purge": 0,
            "convention": "__cdecl"}


# 0x401000 a function with a frame, cut by Ghidra at a call to 0x401020 (taken for one that never returns), holding
# its own piece's address (mov eax, 0x40100f); 0x40100f its epilogue; 0x401020 another function, which jumps to it
OWNER = PUSH_FRAME + bytes.fromhex("b80f104000") + bytes.fromhex("e813000000") + bytes.fromhex("6a00")
PROGRAM = (OWNER + EH_EPILOGUE).ljust(0x20, b"\x90") + PUSH_FRAME + bytes.fromhex("5dc3")
ROWS = [_row(0x401000, 0x40100F), _row(0x40100F, 0x401017), _row(0x401020, 0x401025)]


def test_a_piece_is_delinked_with_the_function_it_belongs_to():
    pe = scratch_pe.read_pe(_tiny_exe(PROGRAM))
    found = scratch_pe.analyse(pe, ROWS)
    assert [f.addr for f in found] == [0x401000, 0x40100F, 0x401020]
    functions, joined = scratch_pe.merge_pieces(pe, found, log=lambda *_: None)
    assert joined == {"_func_00401000": ["_func_0040100F"]}
    whole = functions[0]
    assert (whole.addr, whole.size) == (0x401000, 0x20)  # the epilogue and the padding after it
    # its piece's address is its own now (a label, as delink.py writes $L), the call still a relocation
    assert whole.relocs == [(4, scratch_pe.DIR32, None, 0xF), (9, scratch_pe.REL32, "_func_00401020", 0)]
    # matched functions stay as they are; so does a piece another function calls (Ghidra was right: a function)
    assert scratch_pe.merge_pieces(pe, found, keep={"_func_0040100F"}, log=lambda *_: None)[1] == {}
    called = (OWNER + EH_EPILOGUE).ljust(0x20, b"\x90") + bytes.fromhex("e8eaffffffc3")  # call 0x40100f; ret
    pe = scratch_pe.read_pe(_tiny_exe(called))
    assert scratch_pe.merge_pieces(pe, scratch_pe.analyse(pe, ROWS), log=lambda *_: None)[1] == {}


def test_a_function_found_in_a_gap_keeps_its_last_instruction_whole():
    """A `ret 8` ends with a zero byte, which the gap's padding was stripped with: the byte belonged to no
    function, its function's object ended mid-instruction, and the piece after it was never next to it."""
    code = bytes.fromhex("c3") + bytes.fromhex("33c0c20800") + PUSH_FRAME + bytes.fromhex("5dc3")
    pe = scratch_pe.read_pe(_tiny_exe(code))
    found = scratch_pe.analyse(pe, [_row(0x401000, 0x401001), _row(0x401006, 0x40100B)])
    assert [(f.addr, f.size) for f in found] == [(0x401000, 1), (0x401001, 5), (0x401006, 5)]


def _project(tmp_path: Path, program: bytes, rows: list[dict], unit_bytes: int = 0x1000) -> Path:
    """A PC project's config and delinked objects for a hand-made program, as create writes them (no compiler)."""
    root = tmp_path / "proj"
    (root / "orig").mkdir(parents=True)
    (root / "orig" / "game.exe").write_bytes(_tiny_exe(program))
    pe = scratch_pe.read_pe(_tiny_exe(program))
    groups = {}
    for f in scratch_pe.analyse(pe, rows):  # (one source file per function, to move pieces between them)
        groups[f"text_{f.addr:08X}"] = [scratch_pe._entry(pe, f)]
    info = {"exe": "orig/game.exe", "sha1": hashlib.sha1(_tiny_exe(program)).hexdigest(), "image_base": 0x400000,
            "units": groups}
    (root / "config" / "game").mkdir(parents=True)
    (root / "config" / "game" / "functions.json").write_text(json.dumps(info))
    (root / "src" / "auto").mkdir(parents=True)
    for unit in groups:
        (root / "src" / "auto" / f"{unit}.cpp").write_text('#include "common.h"\n')
    _delink(root)
    return root


def _delink(root: Path) -> None:
    info = json.loads((root / "config" / "game" / "functions.json").read_text())
    exe = (root / info["exe"]).read_bytes()
    out = root / "build" / "game" / "target"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    delink = _template_tool("delink")
    for unit, functions in info["units"].items():
        (out / f"{unit}.obj").write_bytes(delink.coff(functions, exe))


def test_a_project_made_before_gets_its_pieces_joined_and_still_rebuilds(tmp_path):
    root = _project(tmp_path, PROGRAM, ROWS)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]
    # the piece is the first function of its source file: it moves to the file before only when that file keeps
    # other code, and holds no C for it
    assert scratch_pe.merge_project_pieces(root, "game", log=lambda *_: None) == {}
    info = json.loads((root / "config" / "game" / "functions.json").read_text())
    info["units"]["text_0040100F"].append(info["units"].pop("text_00401020")[0])
    (root / "config" / "game" / "functions.json").write_text(json.dumps(info))
    piece_file = root / "src" / "auto" / "text_0040100F.cpp"
    piece_file.write_text("void Obj::func_0040100F(void) {\n}\n")
    assert scratch_pe.merge_project_pieces(root, "game", log=lambda *_: None) == {}
    piece_file.write_text('#include "common.h"\n')
    joined = scratch_pe.merge_project_pieces(root, "game", log=lambda *_: None)
    assert joined == {"_func_00401000": ["_func_0040100F"]}
    units = json.loads((root / "config" / "game" / "functions.json").read_text())["units"]
    assert [[f["name"] for f in fns] for fns in units.values()] == [["_func_00401000"], ["_func_00401020"]]
    assert units["text_00401000"][0]["size"] == 0x20
    _delink(root)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None) == {
        "identical": True, "detail": "identical: the code of its 2 functions (37 bytes)",
        "what": "The game's code, rebuilt from its delinked functions,"}


def test_the_rebuild_check_finds_a_function_delinked_wrong(tmp_path):
    root = _project(tmp_path, PROGRAM, ROWS)
    exe, target = (root / "orig" / "game.exe").read_bytes(), root / "build" / "game" / "target" / "text_00401000.obj"
    owner = json.loads((root / "config" / "game" / "functions.json").read_text())["units"]["text_00401000"][0]
    coff = _template_tool("delink").coff
    target.write_bytes(coff([{**owner, "relocs": [[9, scratch_pe.REL32, "_func_0040100F", 0]]}], exe))  # wrong call
    result = scratch_pe.check_code(root, "game", log=lambda *_: None)
    assert result == {"identical": False,
                      "detail": "1 functions rebuilt from the delinked objects differ from your copy: _func_00401000"}
    target.write_bytes(coff([{**owner, "size": owner["size"] + 1}], exe))  # cut one byte too long
    result = scratch_pe.check_code(root, "game", log=lambda *_: None)
    assert not result["identical"] and "_func_0040100F (overlaps _func_00401000)" in result["detail"]


def test_agents_join_two_functions_by_their_names(tmp_path):
    root = _project(tmp_path, PROGRAM, ROWS)
    with pytest.raises(ForkError, match="is matched"):
        scratch_pe.join_functions(root, "game", "_func_00401000", "_func_0040100F", keep={"_func_0040100F"})
    with pytest.raises(ForkError, match="would be left with no code"):  # (each was given a file of its own)
        scratch_pe.join_functions(root, "game", "_func_00401000", "_func_0040100F")
    info = json.loads((root / "config" / "game" / "functions.json").read_text())
    info["units"]["text_00401000"] += info["units"].pop("text_0040100F")
    (root / "config" / "game" / "functions.json").write_text(json.dumps(info))
    said = scratch_pe.join_functions(root, "game", "_func_0040100F", "_func_00401000")
    assert said == "_func_0040100F joined to _func_00401000"
    _delink(root)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]
    with pytest.raises(ForkError, match="isn't one of the game's functions"):
        scratch_pe.join_functions(root, "game", "_func_0040100F", "_func_00401020")


def test_only_the_frame_helpers_shapes_set_a_frame():
    from decompnet.arch import x86

    after_one = bytes.fromhex("6a0c") + CALL_0 + bytes.fromhex("59c3")  # push 0xc; call _lock; pop ecx; ret
    assert x86.piece(after_one, bytes.fromhex("8b75e4")) is None  # mov esi, [ebp-0x1c]: not this function's
    seh_prolog = bytes.fromhex("6a0c6888204000") + CALL_0 + bytes.fromhex("33dbc3")  # push 0xc; push <table>; call
    assert "frame" in x86.piece(seh_prolog, bytes.fromhex("ff7508c3"))  # push [ebp+8]; ...: its __finally
    eh_prolog3 = bytes.fromhex("6a04b8dd9b8d00") + CALL_0 + bytes.fromhex("c3")  # push 4; mov eax, <h>; call
    assert "frame" in x86.piece(eh_prolog3, CATCH)
    assert x86.piece(bytes.fromhex("53b8dd9b8d00") + CALL_0 + bytes.fromhex("5bc3"), CATCH) is None  # push ebx first


# a function with two jump tables, one right after the other: jmp [eax*4+T1]; jmp [eax*4+T2]; ret; ret; T1; T2
TWO_TABLES = (bytes.fromhex("ff2485") + struct.pack("<I", 0x401010) + bytes.fromhex("ff2485") + struct.pack("<I", 0x401018)
              + bytes.fromhex("c3c3") + struct.pack("<4I", 0x40100E, 0x40100F, 0x40100F, 0x40100E))


def test_a_jump_table_ends_where_the_next_one_starts(tmp_path):
    """Each entry of the second table was relocated twice (23 functions of a PC game): the code rebuilt from the
    objects had those entries doubled. Projects made before get the repeats dropped."""
    pe = scratch_pe.read_pe(_tiny_exe(TWO_TABLES))
    (fn,) = scratch_pe.analyse(pe, [_row(0x401000, 0x401010)])
    assert [r[0] for r in fn.relocs] == [3, 10, 16, 20, 24, 28]  # the tables' addresses, then four entries, once
    root = _project(tmp_path, TWO_TABLES, [_row(0x401000, 0x401010)])
    config = root / "config" / "game" / "functions.json"
    info = json.loads(config.read_text())
    entry = info["units"]["text_00401000"][0]
    entry["relocs"] += entry["relocs"][-2:]  # as analyse made them before
    config.write_text(json.dumps(info))
    _delink(root)
    assert not scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]
    assert scratch_pe.drop_repeated_relocations(root, "game") == 1
    assert scratch_pe.drop_repeated_relocations(root, "game") == 0
    _delink(root)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]


def test_a_project_made_before_gets_its_functions_last_instructions_whole(tmp_path):
    """Found in a gap, `jmp <next function>` (e9 0a 00 00 00) lost its zeros with the padding after it: the function
    was 2 bytes, and no C could match it. 465 functions of a PC game ended mid-instruction."""
    program = bytes.fromhex("c3") + bytes.fromhex("e90a000000") + b"\x90" * 10 + PUSH_FRAME + bytes.fromhex("5dc3")
    rows = [_row(0x401000, 0x401001), _row(0x401010, 0x401015)]
    root = _project(tmp_path, program, rows)
    config = root / "config" / "game" / "functions.json"
    info = json.loads(config.read_text())
    assert info["units"]["text_00401001"][0]["size"] == 15  # (as analyse makes it now)
    info["units"]["text_00401001"][0].update(size=2, relocs=[])  # as it made it before
    config.write_text(json.dumps(info))
    assert scratch_pe.complete_last_instructions(root, "game", keep={"_func_00401001"}) == 0
    assert scratch_pe.complete_last_instructions(root, "game") == 1
    entry = json.loads(config.read_text())["units"]["text_00401001"][0]
    assert entry["size"] == 15 and entry["relocs"] == [[1, scratch_pe.REL32, "_func_00401010", 0]]
    assert scratch_pe.complete_last_instructions(root, "game") == 0
    _delink(root)
    assert scratch_pe.check_code(root, "game", log=lambda *_: None)["identical"]
