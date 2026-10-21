"""Nintendo DS games from the user's own copy (consoles/nds/): telling a ROM, ds-decomp's analysis, and a compiler for
each source file (issue #23: a game built with several CodeWarrior versions got stuck at 80-95% under one), which
try_compiler switches in fork mode. Needs no CodeWarrior: the switch is judged on projects/demo-fork, whose stand-in
compilers are its own GCC with the game's flags and with -O0."""

import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from decompnet import consoles, toolchains
from decompnet.app.engine import RomNetwork
from decompnet.consoles import Compiler
from decompnet.consoles.n64 import N64
from decompnet.consoles.nds import NDS, scratch
from decompnet.forks import csource
from decompnet.forks.project import ForkError, ForkProject
from decompnet.network import NetworkError

ROOT = Path(__file__).resolve().parent.parent


def _header(code: bytes = b"ADQE") -> bytes:
    h = bytearray(0x200)
    h[0:12], h[12:16] = b"DEMOQUESTDS\0", code
    struct.pack_into("<IIII", h, 0x20, 0x4000, 0x02000800, 0x02000000, 0x1000)  # where the ARM9 program sits
    struct.pack_into("<H", h, 0x15E, scratch.crc16(bytes(h[:0x15E])))
    return bytes(h)


def test_a_ds_rom_is_told_by_its_header_and_nothing_else_is(tmp_path):
    rom = tmp_path / "game.nds"
    rom.write_bytes(_header() + bytes(0x4000))
    assert consoles.identify(rom) is NDS
    info = NDS.game_info(rom)
    assert (info["id"], info["title"], info["region"]) == ("ADQE", "DEMOQUESTDS", "North America")
    broken = bytearray(_header())
    broken[0] ^= 1  # the header's checksum no longer agrees
    assert not NDS.sniff(bytes(broken), rom)
    assert "nds" not in {c.id for c in consoles.CONSOLES if c.status == consoles.PUBLIC}


def test_each_source_file_can_have_its_own_codewarrior(tmp_path):
    (tmp_path / ".decomp.wtf.json").write_text(json.dumps({"version": "adqe", "compiler": "DS/2.0/sp1p5"}))
    fp = ForkProject(tmp_path)
    assert NDS.file_compiler(fp, "src/auto/main_02000000.c") is None  # a project 1.0.2 made reads no map
    scratch.compilers_file(tmp_path, "adqe").parent.mkdir(parents=True)
    scratch.compilers_file(tmp_path, "adqe").write_text("{}\n")
    assert NDS.file_compiler(fp, "src/auto/main_02000000.c") == "DS/2.0/sp1p5"
    rel = NDS.set_file_compiler(fp, "src/auto/main_02000000.c", "DS/1.2/sp4")
    assert rel == "config/adqe/arm9/file_compilers.json"
    assert json.loads((tmp_path / rel).read_text()) == {"src/auto/main_02000000.c": "1.2/sp4"}  # as khdays-decomp
    assert NDS.file_compiler(fp, "src/auto/main_02000000.c") == "DS/1.2/sp4"
    NDS.set_file_compiler(fp, "src/auto/main_02000000.c", "DS/2.0/sp1p5")  # the project's own needs no line
    assert json.loads((tmp_path / rel).read_text()) == {}
    with pytest.raises(ForkError):  # a public DS decomp's own build sets its compilers
        NDS.set_file_compiler(ForkProject(tmp_path / "public"), "src/a.c", "DS/1.2/sp4")


def test_a_call_to_code_dsd_found_no_function_for_is_analysed_again(tmp_path, monkeypatch):
    """A DS game calls 0x0206135C, which dsd's analysis didn't take for a function ("Local
    function call ... leads to no function"): the analysis runs again with dsd's option for such calls."""
    runs = []

    def run(cmd, cwd, what):
        runs.append(cmd)
        if "--allow-unknown-function-calls" not in cmd:
            raise ForkError("analysing the game failed:\n[ERROR] Local function call from 0x0204f346 in ARM9 main to "
                            "0x0206135c leads to no function")
    monkeypatch.setattr(scratch, "_run", run)
    (tmp_path / "config" / "cs3e").mkdir(parents=True)  # what the failed run left
    said = []
    scratch._init("dsd", tmp_path, "cs3e", said.append)
    assert len(runs) == 2 and runs[1] == [*runs[0], "--allow-unknown-function-calls"]
    assert not (tmp_path / "config" / "cs3e").exists() and "ds-decomp didn't find" in said[0]

    monkeypatch.setattr(scratch, "_run", lambda *a: (_ for _ in ()).throw(ForkError("analysing the game failed: other")))
    with pytest.raises(ForkError, match="other"):  # anything else still stops the import
        scratch._init("dsd", tmp_path, "cs3e", said.append)


# ---------------------------------------------------------------------------------------------- try_compiler

FORK_SRC = ROOT / "projects" / "demo-fork"
ROM = ROOT / "src" / "decompnet" / "app" / "static" / "demo" / "demo-quest-64.z64"
GOOD, BAD = "N64/game-flags", "N64/O0"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(cwd)})


@pytest.fixture
def per_file(tmp_path, monkeypatch):
    """projects/demo-fork whose console lets src/game.c have one of two compilers: GCC with the game's flags (GOOD)
    or with -O0 (BAD), set by rewriting the project's build.ninja."""
    if toolchains.installed("gcc-2.7.2-kmc") is None or toolchains.installed("objdiff-cli") is None or not shutil.which("git"):
        pytest.skip("needs gcc-2.7.2-kmc, objdiff-cli and git")
    upstream = tmp_path / "upstream"
    shutil.copytree(FORK_SRC, upstream)
    _git(upstream, "init", "-q", "-b", "main")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-q", "-m", "initial")
    net = RomNetwork(tmp_path / "app.db", tmp_path / "work")
    (tmp_path / "work").mkdir()
    slug = net.forks.clone(str(upstream), name="Demo Quest decomp", platform="n64", log=lambda *_: None)
    net.forks.setup(slug, ROM.read_bytes(), "demo.z64", log=lambda *_: None)
    root = Path(net.project_row(slug)["root"])
    state = {"compiler": GOOD, "sets": []}

    def set_file_compiler(fp, source, compiler):
        ninja = root / "build.ninja"
        flags = " -O2" if compiler == GOOD else " -O0"
        ninja.write_text(ninja.read_text().replace(" -O2" if state["compiler"] == GOOD else " -O0", flags))
        state["compiler"] = compiler
        state["sets"].append(compiler)
        return "build.ninja"
    monkeypatch.setattr(N64, "compilers", (Compiler(GOOD, "the game's"), Compiler(BAD, "-O0")))
    monkeypatch.setattr(N64, "file_compiler", lambda fp, source: state["compiler"] if source in ("", "src/game.c") else None)
    monkeypatch.setattr(N64, "set_file_compiler", set_file_compiler)
    agent, _ = net.local_agent("claude-1")
    lerp = csource.get((FORK_SRC / "tools" / "reference" / "game.c").read_text(), "lerp").strip()
    return net, slug, agent, root, state, lerp


def test_a_compiler_that_doesnt_match_leaves_the_file_as_it_was(per_file):
    net, slug, agent, root, state, lerp = per_file
    before = (root / "src" / "game.c").read_text()
    said = net.try_compiler(agent, slug, "lerp", BAD, source=lerp)
    # it compiled with -O0, and the file's matched functions don't match with it: refused, whatever lerp did
    assert "clamp_s32" in said and "split_file" in said and "BUILD ERROR" not in said, said
    assert "stays with N64/game-flags" in said and state["compiler"] == GOOD and " -O2" in (root / "build.ninja").read_text()
    assert (root / "src" / "game.c").read_text() == before
    rows = {r["name"]: r for r in net.function_map(slug)}
    assert rows["lerp"]["status"] == "open" and rows["clamp_s32"]["status"] == "matched"
    assert net.store.one("SELECT COUNT(*) AS n FROM attempts")["n"] == 0  # tried, recorded nowhere
    with pytest.raises(NetworkError, match="already"):
        net.try_compiler(agent, slug, "lerp", GOOD, source=lerp)
    with pytest.raises(NetworkError, match="flags"):
        net.try_compiler(agent, slug, "lerp", BAD, flags="-O1", source=lerp)
    assert "each source file can have its own compiler" in net.forks.info(slug)
    said = net.try_compiler(agent, slug, "lerp", "all", source=lerp)  # its file's own compiler goes first
    assert "nothing to switch" in said and state["sets"] == [BAD, GOOD], said


def test_the_compiler_a_function_matches_with_becomes_its_files(per_file):
    net, slug, agent, root, state, lerp = per_file
    set_file_compiler = N64.set_file_compiler
    set_file_compiler(None, "src/game.c", BAD)  # the file was built with the wrong one so far
    said = net.try_compiler(agent, slug, "lerp", "all", source=lerp)
    assert "MATCH" in said and "built with N64/game-flags from now on" in said, said
    assert state["compiler"] == GOOD and state["sets"][-1] == GOOD
    assert {r["name"]: r for r in net.function_map(slug)}["lerp"]["status"] == "matched"
    assert "lerp" in (root / "src" / "game.c").read_text()


def test_the_projects_build_compiles_each_file_with_its_version(tmp_path):
    """tools/configure.py writes each source file's compile with the version file_compilers.json names, the
    project's own otherwise, and builds build.ninja again when the map changes."""
    import sys

    root = tmp_path / "game"
    shutil.copytree(scratch.TEMPLATE, root)
    config = root / "config" / "adqe" / "arm9"
    config.mkdir(parents=True)
    (config / "delinks.txt").write_text("")
    (config / "file_compilers.json").write_text(json.dumps({"src/auto/main_02000400.c": "1.2/sp4"}))
    for name in ("main_02000000", "main_02000400"):
        (root / "src" / "auto").mkdir(parents=True, exist_ok=True)
        (root / "src" / "auto" / f"{name}.c").write_text('#include "common.h"\n')
    compilers = tmp_path / "mwccarm"
    for version in ("2.0/sp1p5", "1.2/sp4"):
        (compilers / version).mkdir(parents=True)
        (compilers / version / "mwccarm.exe").write_text("")
    dsd = tmp_path / "dsd.py"  # what `dsd json delinks` says, for a project with nothing delinked yet
    dsd.write_text(f"#!{sys.executable}\nimport json\nprint(json.dumps({{'files': [], 'arm9_lcf_file': 'build/arm9.lcf', "
                   "'arm9_objects_file': 'build/objects.txt'}))\n")
    subprocess.run([sys.executable, "tools/configure.py", "adqe", "--dsd", str(dsd), "--compilers", str(compilers)],
                   cwd=root, check=True, capture_output=True)
    ninja = re.sub(r"\$\n\s*", "", (root / "build.ninja").read_text()).replace("\\", "/")

    def compiler_of(name: str) -> str:
        block = ninja.split(f"build build/adqe/src/auto/{name}.o: mwcc", 1)[1].split("\nbuild ", 1)[0]
        return block.split("cc = ", 1)[1].split("\n", 1)[0]
    assert compiler_of("main_02000000").endswith("2.0/sp1p5/mwccarm.exe")
    assert compiler_of("main_02000400").endswith("1.2/sp4/mwccarm.exe")
    assert "config/adqe/arm9/file_compilers.json" in ninja.split("build build.ninja: configure", 1)[1].split("\n", 1)[0]


def test_arms_pc_bias_isnt_shown_as_an_offset():
    """objdiff showed "bl func_0203aca8-0x8": the -8 is the pipeline's, not an offset into the function."""
    from decompnet.forks import objdiff

    def row(text, kind, addend):
        return {"instruction": {"formatted": text, "relocation": {"type_name": kind, "addend": addend}}}
    assert objdiff._fmt(row("bl func_0203aca8-0x8", "R_ARM_PC24", "-8")) == "bl func_0203aca8"
    assert objdiff._fmt(row("bl func_02001000-0x4", "R_ARM_THM_CALL", "-4")) == "bl func_02001000"
    assert objdiff._fmt(row("bl func_0203aca8-0x10", "R_ARM_PC24", "-16")) == "bl func_0203aca8-0x10"  # a real one
    assert objdiff._fmt(row("lui at, %hi(x-0x8)", "R_MIPS_HI16", "-8")) == "lui at, %hi(x-0x8)"


def test_a_from_scratch_brief_says_what_the_data_it_uses_is(per_file, monkeypatch):
    """Its headers start empty: on a DS game a model looked in config/symbols.txt for minutes for what a global
    was. The brief says where each one is and how big (read_data's answer on that game), and leaves functions out."""
    net, slug, agent, root, state, lerp = per_file
    proj = net.project_row(slug)

    def read_data(agent, slug, what, length=64):
        if what == "data_02225058":
            return "data_02225058 (_dsd_gap@main_2, .bss+0x107CF8, 2 bytes): uninitialized data (.bss), zeros at boot."
        raise NetworkError(f"no data symbol `{what}`")
    monkeypatch.setattr(net.forks, "read_data", read_data)
    lines = net.forks._data_used(proj, ["add_score", "data_02225058", "g_score"])
    assert lines[1:4] == ["### Data it uses", "- data_02225058: .bss, 2 bytes", "- g_score"]
    assert not any("add_score" in line for line in lines) and "extern" in lines[-1]
    assert net.forks._data_used(proj, ["add_score"]) == []


def test_decomp_permuter_reads_a_forks_function_with_its_files_declarations(per_file):
    """In a fork the permuter gave decomp-permuter no header, so it refused every DS function ("Syntax error in
    base.c": `u16` was no type it knew) and only the simple rewrites ran. Now it reads what the file includes and
    declares: add_score uses demo-fork's s32, its Player struct and globals from game.h."""
    from decompnet import seeds
    from decompnet.app import permute

    if toolchains.installed(permute.TOOL) is None:
        pytest.skip("needs decomp-permuter")
    net, slug, agent, root, state, lerp = per_file
    source = csource.get((FORK_SRC / "tools" / "reference" / "game.c").read_text(), "add_score").strip()
    header = seeds.c_only(net.forks.permuter_header(slug, "add_score"))
    assert "typedef signed int s32;" in header and "void add_score(s32 points);" in header
    job = {"source": source, "function": "add_score", "family": "gcc", "seed": 1, "count": 8}
    gen = permute.Generator()
    try:
        assert gen.batch({**job, "header": ""}) is None  # what it got before
        variants = gen.batch({**job, "header": header})
        assert variants and all("add_score(" in v for v in variants)
    finally:
        gen.close()


def test_the_projects_compiler_changes_with_every_file_keeping_its_own(tmp_path):
    """The Settings panel's compiler for the whole game: it becomes the project's own (configure.py, the manifest)
    and files built with another keep theirs, the ones on the old one included. Its address ranges are the files'
    own lines in delinks.txt."""
    root = tmp_path / "game"
    shutil.copytree(scratch.TEMPLATE, root)
    arm9 = root / "config" / "adqe" / "arm9"
    (arm9 / "overlays" / "ov000").mkdir(parents=True)
    (arm9 / "delinks.txt").write_text("    .text       start:0x02000000 end:0x02000800 kind:code align:32\n\n"
                                      "src/auto/main_02000000.c:\n    .text start:0x02000000 end:0x02000400\n\n"
                                      "src/auto/main_02000400.c:\n    .text start:0x02000400 end:0x02000800\n")
    (arm9 / "overlays" / "ov000" / "delinks.txt").write_text("src/auto/ov000_02200000.c:\n    .text start:0x02200000 end:0x02200100\n")
    assert scratch.file_ranges(root, "adqe") == {"src/auto/main_02000000.c": [(0x02000000, 0x02000400)],
                                                 "src/auto/main_02000400.c": [(0x02000400, 0x02000800)],
                                                 "src/auto/ov000_02200000.c": [(0x02200000, 0x02200100)]}
    (arm9 / "file_compilers.json").write_text(json.dumps({"src/auto/main_02000400.c": "1.2/sp4"}))
    manifest = {"version": "adqe", "compiler": "DS/2.0/sp1p5"}
    sources = sorted(scratch.file_ranges(root, "adqe"))
    changed = scratch.set_own_compiler(root, manifest, "DS/1.2/sp4", sources)
    assert set(changed) == {"config/adqe/arm9/file_compilers.json", "tools/configure.py", ".decomp.wtf.json"}
    # main_02000400 was built with 1.2sp4 already: no line; the two others stay on 2.0sp1p5, so they get one
    assert json.loads((arm9 / "file_compilers.json").read_text()) == {"src/auto/main_02000000.c": "2.0/sp1p5",
                                                                      "src/auto/ov000_02200000.c": "2.0/sp1p5"}
    assert 'MWCC_VERSION = "1.2/sp4"' in (root / "tools" / "configure.py").read_text()
    assert json.loads((root / ".decomp.wtf.json").read_text())["compiler"] == "DS/1.2/sp4"


def test_the_settings_panel_switches_files_only_where_their_matches_hold(per_file, monkeypatch):
    """The project Settings' compiler, as for a ROM project: for address ranges (the files whose code is in them)
    or for the whole game (every file in turn), each file kept only when its matched functions still match."""
    net, slug, agent, root, state, lerp = per_file
    monkeypatch.setattr(N64, "file_ranges", lambda fp: {"src/game.c": [(0x80000400, 0x80000800)]})
    owned = []
    monkeypatch.setattr(N64, "set_own_compiler", lambda fp, compiler, sources: owned.append((compiler, sources)) or [])
    ov = net.overview(slug)
    assert ov["compiler"]["toolchain"] == GOOD and ov["compilers"] == [GOOD, BAD] and ov["compiler_ranges"] == []
    assert net.compiler_answer(slug)["parts"] == []
    with pytest.raises(NetworkError, match="no longer match"):  # -O0 breaks game.c's matched functions
        net.forks.set_compiler_ranges(slug, [[0x80000500, 0x80000600]], BAD)
    with pytest.raises(NetworkError, match="no source file"):
        net.forks.set_compiler_ranges(slug, [[0x90000000, 0x90000100]], BAD)
    said = net.forks.set_compiler_everywhere(slug, BAD, log=lambda *_: None)
    assert said["switched"] == 0 and len(said["stayed"]) == 1 and state["compiler"] == GOOD
    assert owned == [(BAD, ["src/game.c"])]  # the project's own now, game.c keeping its compiler
    assert {r["name"]: r for r in net.function_map(slug)}["clamp_s32"]["status"] == "matched"
    # the project's own is BAD now, game.c kept GOOD: a part of its own, in the overview as in the answer
    monkeypatch.setattr(N64, "file_compiler", lambda fp, source: BAD if source == "" else GOOD)
    assert [(p["file"], p["start"]) for p in net.overview(slug)["compiler_ranges"]] == [("src/game.c", 0x80000400)]
    assert [p["file"] for p in net.compiler_answer(slug)["parts"]] == ["src/game.c"]


def test_bios_call_stubs_are_set_aside_as_hand_written():
    """A DS game: 19 BIOS call stubs (CpuSet, Div, WaitByLoop...: `svc #n; bx lr`) were
    open functions, and agents spent 30 tries finding each can't be C. Their bytes, as dsd delinked them."""
    from decompnet import arch

    for stub in ("0cdf7047", "00df7047", "002205df7047", "09df081c7047"):  # CpuFastSet, SoftReset, VBlankIntrWait, Mod
        assert "BIOS" in NDS.asm_reason(bytes.fromhex(stub)), stub
    swi_arm = (0xEF00000B).to_bytes(4, "little") + (0xE12FFF1E).to_bytes(4, "little")
    assert "swi" in arch.get("arm").asm_reason(swi_arm)
    assert NDS.asm_reason(bytes.fromhex("1eff2fe1")) is None  # AutoloadCallback: an empty function, which C makes
    assert NDS.asm_reason(bytes.fromhex("0cdf7047") + bytes(16)) is None  # a longer function: no guessing


def test_data_in_a_code_section_isnt_a_function(per_file, monkeypatch):
    """objdiff's report counted BuildInfo (the DS SDK's build information, data in .text) as a function, and
    agents tried to write it. A target object's data symbols are left out of the functions."""
    from decompnet.app import forkengine

    net, slug, agent, root, state, lerp = per_file
    target = net.forks.get(slug)[1].unit("game").target
    assert "g_score" in forkengine._data_symbols(target) and "lerp" not in forkengine._data_symbols(target)
    assert forkengine._data_symbols(None) == set()
    monkeypatch.setattr(forkengine, "_data_symbols", lambda target: {"lerp"})  # as if lerp were data
    net.forks.sync(slug, log=lambda *_: None)
    assert "lerp" not in {r["name"] for r in net.function_map(slug)}


def test_start_up_code_that_sets_the_cpus_mode_is_hand_written():
    """A DS program's Entry switches the CPU's mode (msr cpsr) and sets up its cache (mcr p15): no compiler emits
    either. Only in code known to be ARM: Thumb code read as ARM words could look like them."""
    from decompnet.arch import arm

    words = lambda *ws: b"".join(w.to_bytes(4, "little") for w in ws)  # noqa: E731
    msr = words(0xE3A00013, 0xE121F000, 0xE12FFF1E) + bytes(16)  # mov r0, #0x13; msr cpsr_c, r0; bx lr
    mcr = words(0xEE070F9A, 0xE12FFF1E) + bytes(16)  # mcr p15, 0, r0, c7, c10, 4; bx lr
    assert "msr" in arm.asm_reason(msr, thumb=False) and "p15" in arm.asm_reason(mcr, thumb=False)
    assert arm.asm_reason(msr, thumb=True) is None and arm.asm_reason(msr) is None  # Thumb, or not known
    assert arm.asm_reason(words(0xE0800001, 0xE12FFF1E) + bytes(16), thumb=False) is None  # add r0, r0, r1: C


def test_pieces_of_functions_are_set_aside_and_whole_functions_are_not():
    """On a DS game 159 of 557 open functions were pieces of others (ds-decomp's symbols in the wrong place), each
    given up on by five to seven agents: a lone epilogue, a register save running on into the next symbol, one
    instruction from the middle of a function, a lone `bx ip`. No function the game's agents matched reads as one."""
    from decompnet.arch import arm

    words = lambda *ws: b"".join(w.to_bytes(4, "little") for w in ws)  # noqa: E731
    halves = lambda *hs: b"".join(h.to_bytes(2, "little") for h in hs)  # noqa: E731
    assert "the end of" in arm.asm_reason(words(0xE8BD8010), thumb=False)  # pop {r4, pc}
    assert "the start of" in arm.asm_reason(words(0xE92D000F), thumb=False)  # push {r0-r3}, then the next symbol
    assert "never returns" in arm.asm_reason(words(0xE58DC004), thumb=False)  # str ip, [sp, #4]
    assert "veneer" in arm.asm_reason(words(0xE12FFF1C), thumb=False)  # bx ip
    # the caller's registers: read before set (past a switch's first branch), or set without saving them first
    assert "reads r4" in arm.asm_reason(words(0xE3530007, 0x0A000000, 0xEA000000, 0xE5940000, 0xE12FFF1E), thumb=False)
    assert "sets r5" in arm.asm_reason(words(0xE3A05001, 0xE12FFF1E), thumb=False)  # mov r5, #1; bx lr
    assert "the end of" in arm.asm_reason(halves(0xBD10), thumb=True)  # Thumb pop {r4, pc}
    whole = [(words(0xE92D4010, 0xE1A04000, 0xE8BD8010), False),  # push {r4, lr}; mov r4, r0; pop {r4, pc}
             (words(0xE12FFF1E), False),  # bx lr: an empty function
             (words(0xE59FC000, 0xE12FFF1C, 0), False),  # ldr ip, [pc]; bx ip; .word: CodeWarrior's tail call
             (words(0xEA000000), False),  # b elsewhere: a tail call
             (words(0xE92D4000, 0xEB000000), False),  # push {lr}; bl: a call that may never return
             (halves(0xB510, 0xBD10), True),  # Thumb push {r4, lr}; pop {r4, pc}
             (words(0xE92D4010, 0xE5940000, 0xE8BD8010), False),  # push {r4, lr}; ldr r0, [r4]; pop {r4, pc}
             (words(0xE59F0000, 0xE12FFF1E, 0xE5940000), False)]  # ldr r0, [pc]; bx lr; a pool word reading as ldr [r4]
    for code, thumb in whole:
        assert arm.asm_reason(code, thumb=thumb) is None, code.hex()
    assert arm.asm_reason(words(0xE8BD8010)) is None  # its mode unknown: not judged


BASE = 0x02100000


def _module(tmp_path):
    """A main program dsd half read: it found `a` (bx lr) but not `b` (it calls a, and nothing calls it), nor `c`
    (a leaf only a pointer in data reaches); after them, data whose bytes look like a Thumb push."""
    from decompnet.consoles.nds import recover

    words = [0xE12FFF1E,  # a: bx lr
             0xE92D4000, 0xEBFFFFFC, 0xE8BD8000,  # b: stmdb sp!, {lr}; bl a; ldmia sp!, {pc}
             0xE3A00001, 0xE12FFF1E,  # c: mov r0, #1; bx lr
             0, 0xB50C0000]  # data: 0000, then 0xB50C (push {r2, r3, lr}) and more zeros
    code = b"".join(w.to_bytes(4, "little") for w in words) + bytes(8)
    code += (BASE + 0x10).to_bytes(4, "little")  # .data: a pointer to c
    folder = tmp_path / "arm9"
    folder.mkdir()
    (folder / "symbols.txt").write_text(f"func_{BASE:08x} kind:function(arm,size=0x4) addr:{BASE:#010x}\n")
    (folder / "relocs.txt").write_text("")
    m = recover.Module("main", "main", folder, BASE, code, [(BASE, BASE + 0x28)], covered=bytearray(len(code)))
    m.symbol_lines = (folder / "symbols.txt").read_text().splitlines()
    m.symbols.add(BASE)
    m.functions[BASE] = (4, False)
    m.cover(BASE, 4)
    m.dsds = bytes(m.covered)  # (as load_modules: what dsd found)
    return m



def test_pieces_of_functions_are_joined_to_the_function_they_belong_to(tmp_path, monkeypatch):
    """dsd put symbols in the middle of functions: on a DS game 119 pieces were joined to their functions this way,
    4 of those functions matched at once with the C agents had written, none lost its match, and the ROM still
    rebuilt byte for byte. A piece that runs on goes with the code after it, an epilogue with the code before; a
    matched function is left as it is, and a piece's symbol stays as a label for what points to it."""
    from decompnet.consoles.nds import recover

    words = [0xE92D4010, 0xE1A04000,  # f: push {r4, lr}; mov r4, r0 (runs on)
             0xE1A00004,  # g: mov r0, r4 (runs on)
             0xE8BD8010,  # h: pop {r4, pc} (an epilogue alone)
             0xE92D000F,  # v: push {r0-r3}, running on into a matched function
             0xE12FFF1E]  # w: bx lr (matched)
    folder = tmp_path / "arm9"
    folder.mkdir()
    names = {BASE: ("f", 8), BASE + 8: ("g", 4), BASE + 0xC: ("h", 4), BASE + 0x10: ("v", 4), BASE + 0x14: ("w", 4)}
    lines = [f"{n} kind:function(arm,size={s:#x}) addr:{a:#010x}" for a, (n, s) in names.items()]
    (folder / "symbols.txt").write_text("\n".join(lines) + "\n")
    (folder / "delinks.txt").write_text(f"    .text start:{BASE:#010x} end:{BASE + 0x18:#010x} kind:code\n\n"
                                        f"src/a.c:\n    .text start:{BASE:#010x} end:{BASE + 0x18:#010x}\n")
    m = recover.Module("main", "main", folder, BASE, b"".join(w.to_bytes(4, "little") for w in words),
                       [(BASE, BASE + 0x18)], symbol_lines=lines)
    m.functions = {a: (s, False) for a, (_, s) in names.items()}
    monkeypatch.setattr(recover, "load_modules", lambda config, extract: [m])
    assert recover.merge_pieces(tmp_path, "v1", keep={"w"}, log=lambda *_: None) == {"f": ["g", "h"]}
    assert (folder / "symbols.txt").read_text().splitlines() == [
        f"f kind:function(arm,size=0x10) addr:{BASE:#010x}",
        f"g kind:label(arm) addr:{BASE + 8:#010x}",
        f"h kind:label(arm) addr:{BASE + 0xC:#010x}",
        lines[3], lines[4]]

def test_the_functions_dsd_misses_are_found_with_their_calls(tmp_path):
    """dsd parses a module's functions one after another until a pointer to data stops it, then follows calls: a
    function only a pointer reaches, and what it calls, stayed in no function (more than half of a DS game's code)."""
    from decompnet.consoles.nds import recover

    m = _module(tmp_path)
    assert recover.find_functions([m], log=lambda *_: None) == 2
    assert m.added == {BASE + 4: (12, False, True), BASE + 0x10: (8, False, True)}  # not the data's "push"
    recover.relocations([m])
    assert m.relocs[BASE + 8] == f"from:{BASE + 8:#010x} kind:arm_call to:{BASE:#010x} module:main"
    assert m.relocs[BASE + 0x28] == f"from:{BASE + 0x28:#010x} kind:load to:{BASE + 0x10:#010x} module:main"
    recover.write([m])
    symbols = (m.folder / "symbols.txt").read_text().splitlines()
    assert symbols == [f"func_{BASE:08x} kind:function(arm,size=0x4) addr:{BASE:#010x}",
                       f"func_{BASE + 4:08x} kind:function(arm,size=0xc) addr:{BASE + 4:#010x}",
                       f"func_{BASE + 0x10:08x} kind:function(arm,size=0x8) addr:{BASE + 0x10:#010x}"]
    recover.write([m])  # written again (dsd refused one, say): from dsd's own lines, not twice
    assert (m.folder / "symbols.txt").read_text().splitlines() == symbols


def test_a_function_dsd_refuses_is_dropped_with_what_points_to_it(tmp_path):
    import sys

    from decompnet.consoles.nds import recover

    m = _module(tmp_path)
    recover.find_functions([m], log=lambda *_: None)
    recover.relocations([m])
    (tmp_path / "config" / "v" / "arm9").mkdir(parents=True)
    dsd = tmp_path / "dsd.py"  # refuses c once, then delinks
    dsd.write_text(f"#!{sys.executable}\nimport pathlib, sys\nflag = pathlib.Path({str(tmp_path / 'once')!r})\n"
                   "if not flag.exists():\n    flag.write_text('')\n"
                   f"    print('Error: function func_{BASE + 0x10:08x} could not be analyzed: InvalidStart {{ }}')\n"
                   "    sys.exit(1)\n")
    assert recover.check_with_dsd(tmp_path, "v", str(dsd), [m], log=lambda *_: None) == 1
    assert list(m.added) == [BASE + 4] and BASE + 0x28 not in m.relocs  # the pointer to it went with it
    assert f"func_{BASE + 0x10:08x}" not in (m.folder / "symbols.txt").read_text()


def test_code_dsd_took_for_rodata_joins_text_with_its_calls(tmp_path):
    """dsd ends .text at the last function it found: code right after it that only pointers reach was .rodata to
    it, and calls into it from .text had no relocation (on a DS game, 45 KB and 19 calls). .text now ends where that
    code does, its functions are found, and the calls into them relocated; the data after it stays .rodata."""
    from decompnet.consoles.nds import recover, scratch

    words = [0xE92D4000, 0xEB000000, 0xE8BD8000,  # a (dsd's): stmdb sp!, {lr}; bl d; ldmia sp!, {pc}
             0xE3A00001, 0xE12FFF1E,  # d, past .text's end: mov r0, #1; bx lr
             0xE3A00002, 0xE12FFF1E,  # e: mov r0, #2; bx lr
             0x00000010, 0x00000020]  # data
    code = b"".join(w.to_bytes(4, "little") for w in words)
    folder = tmp_path / "config" / "v" / "arm9"
    folder.mkdir(parents=True)
    (folder / "symbols.txt").write_text(f"func_{BASE:08x} kind:function(arm,size=0xc) addr:{BASE:#010x}\n"
                                        f"data_{BASE + 0xc:08x} kind:data(any) addr:{BASE + 0xc:#010x}\n"
                                        f"data_{BASE + 0x1c:08x} kind:data(any) addr:{BASE + 0x1c:#010x}\n")
    (folder / "relocs.txt").write_text("")
    (folder / "delinks.txt").write_text(
        f"    .text       start:{BASE:#010x} end:{BASE + 0xc:#010x} kind:code align:32\n"
        f"    .rodata     start:{BASE + 0xc:#010x} end:{BASE + 0x24:#010x} kind:rodata align:4\n\n"
        f"src/auto/main_{BASE:08X}.c:\n    .text start:{BASE:#010x} end:{BASE + 0xc:#010x}\n")
    m = recover.Module("main", "main", folder, BASE, code, [(BASE, BASE + 0xc)], covered=bytearray(len(code)),
                       rodata=(BASE + 0xc, BASE + 0x24))
    m.symbol_lines = (folder / "symbols.txt").read_text().splitlines()
    m.symbols |= {BASE, BASE + 0xc, BASE + 0x1c}
    m.functions[BASE] = (0xc, False)
    m.cover(BASE, 0xc)
    m.dsds = bytes(m.covered)

    assert recover.extend_text(m, log=lambda *_: None) == 0x10 and m.text == [(BASE, BASE + 0x1c)]
    recover.find_functions([m], log=lambda *_: None)
    assert m.added == {BASE + 0xc: (8, False, True), BASE + 0x14: (8, False, True)}
    recover.relocations([m])
    assert m.relocs[BASE + 4] == f"from:{BASE + 4:#010x} kind:arm_call to:{BASE + 0xc:#010x} module:main"
    recover.write([m])
    head = (folder / "delinks.txt").read_text().split("\n\n")[0]
    assert f".text       start:{BASE:#010x} end:{BASE + 0x1c:#010x}" in head
    assert f".rodata     start:{BASE + 0x1c:#010x} end:{BASE + 0x24:#010x}" in head
    symbols = (folder / "symbols.txt").read_text()
    assert f"data_{BASE + 0xc:08x}" not in symbols and f"data_{BASE + 0x1c:08x}" in symbols  # code now, and data

    (tmp_path / "src" / "auto").mkdir(parents=True)  # the code .text gained gets a source file of its own
    assert scratch.add_sources(tmp_path, "v") == [f"src/auto/main_{BASE + 0xc:08X}.c"]
    assert f"src/auto/main_{BASE + 0xc:08X}.c:\n    .text start:{BASE + 0xc:#010x} end:{BASE + 0x1c:#010x}" in \
        (folder / "delinks.txt").read_text()
    assert '#include "common.h"' in (tmp_path / "src" / "auto" / f"main_{BASE + 0xc:08X}.c").read_text()
    assert scratch.add_sources(tmp_path, "v") == []


def test_code_after_text_that_nothing_calls_stays_rodata(tmp_path):
    from decompnet.consoles.nds import recover

    code = b"".join(w.to_bytes(4, "little") for w in [0xE12FFF1E, 0xE3A00001, 0xE12FFF1E, 0x10, 0x20])
    m = recover.Module("main", "main", tmp_path, BASE, code, [(BASE, BASE + 4)], covered=bytearray(len(code)),
                       rodata=(BASE + 4, BASE + 0x14))
    m.functions[BASE] = (4, False)
    m.cover(BASE, 4)
    assert recover.extend_text(m, log=lambda *_: None) == 0 and m.text == [(BASE, BASE + 4)]


def test_a_project_is_analysed_again_and_says_so(per_file):
    """A console's better analysis reaches projects made before it (Console.on_start): what it found is in the
    project's feed, and an analysis that fails leaves the project as it was."""
    net, slug, agent, root, state, lerp = per_file
    assert net.forks.reanalyse(slug, lambda fp: None, log=lambda *_: None) is None  # nothing to change
    assert net.forks.reanalyse(slug, lambda fp: "3 more functions", log=lambda *_: None) == "3 more functions"

    def broken(fp):
        raise ForkError("dsd refused it")
    assert net.forks.reanalyse(slug, broken, log=lambda *_: None) is None
    said = [r["detail"] for r in net.store.all("SELECT detail FROM events WHERE kind='analysis' ORDER BY id")]
    assert said[0] == "3 more functions" and "stays as it was" in said[1] and "dsd refused it" in said[1]

    def tools(fp):  # a fix to the project's build tools is committed with it, and taken back with it
        (fp.root / "tools" / "reference" / "game.c").write_text("/* fixed */\n")
        return "its build is fixed"
    assert net.forks.reanalyse(slug, tools, paths=("config", "tools"), log=lambda *_: None) == "its build is fixed"
    assert ForkProject(root).git("status", "--porcelain", "--", "tools")[1].strip() == ""

    def tools_broken(fp):
        (fp.root / "tools" / "reference" / "game.c").write_text("/* half done */\n")
        raise ForkError("dsd refused it")
    assert net.forks.reanalyse(slug, tools_broken, paths=("config", "tools"), log=lambda *_: None) is None
    assert (root / "tools" / "reference" / "game.c").read_text() == "/* fixed */\n"

    def sources(fp):  # files a step adds (source files for code it found) go with it: kept, or taken back
        (fp.root / "src" / "found.c").write_text("/* new */\n")
        return "a new source file"
    assert net.forks.reanalyse(slug, sources, log=lambda *_: None) == "a new source file"
    assert ForkProject(root).git("ls-files", "src/found.c")[1] == "src/found.c"

    def sources_broken(fp):
        (fp.root / "src" / "half.c").write_text("/* half */\n")
        raise ForkError("dsd refused it")
    assert net.forks.reanalyse(slug, sources_broken, log=lambda *_: None) is None
    assert not (root / "src" / "half.c").exists()


def test_an_older_projects_build_tools_get_the_fixes_and_keep_its_settings(tmp_path):
    """Its tools/ came from the template when it was made: dead stripping in its link dropped every overlay holding
    only data (nothing points at one: the game loads it by number), so the overlays after it moved and the rebuild
    differed. write_tools brings them up to date, keeping the game and compiler its configure.py names."""
    (tmp_path / "tools").mkdir()
    old = (scratch.TEMPLATE / "tools" / "configure.py").read_text().replace('"-nodead"', '"-dead"')
    old = re.sub(r'^GAME = "[^"]*"', 'GAME = "c32p"', old, flags=re.M)
    (tmp_path / "tools" / "configure.py").write_text(re.sub(r'^MWCC_VERSION = "[^"]*"', 'MWCC_VERSION = "1.2/sp4"',
                                                            old, flags=re.M))
    assert "tools/configure.py" in scratch.write_tools(tmp_path)
    new = (tmp_path / "tools" / "configure.py").read_text()
    assert '"-nodead"' in new and 'GAME = "c32p"' in new and 'MWCC_VERSION = "1.2/sp4"' in new
    assert (tmp_path / "tools" / "same.py").exists() and scratch.write_tools(tmp_path) == []


def test_the_rebuilt_rom_carries_the_secure_area_checksum_of_the_copy(tmp_path):
    """The header's secure area checksum is of the area encrypted; a dump holds it decrypted, as dsd builds it, and
    dsd leaves the field 0 (encrypting needs the console's BIOS). With the same secure area, the build takes the
    copy's checksum, so the ROM comes out byte for byte (tools/same.py --header)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ds_same", scratch.TEMPLATE / "tools" / "same.py")
    same = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(same)
    original = bytearray(_header() + bytes(range(256)) * 64)
    struct.pack_into("<H", original, 0x6C, 0xA429)
    struct.pack_into("<H", original, 0x15E, scratch.crc16(bytes(original[:0x15E])))
    built = bytearray(original)
    struct.pack_into("<H", built, 0x6C, 0)
    struct.pack_into("<H", built, 0x15E, scratch.crc16(bytes(built[:0x15E])))
    (tmp_path / "o.nds").write_bytes(original)
    (tmp_path / "b.nds").write_bytes(built)
    assert same.main(["--header", str(tmp_path / "b.nds"), str(tmp_path / "o.nds")]) == 0
    assert (tmp_path / "b.nds").read_bytes() == bytes(original)
    built[0x4100] ^= 0xFF  # a secure area of its own: the checksum stays dsd's
    (tmp_path / "b.nds").write_bytes(built)
    same.main(["--header", str(tmp_path / "b.nds"), str(tmp_path / "o.nds")])
    assert (tmp_path / "b.nds").read_bytes() == bytes(built)


def test_a_function_pieces_were_joined_to_says_so_in_a_note(tmp_path, monkeypatch):
    """Its notes from before the join say "fragment, not matchable standalone, skip": agents read them and skipped a
    function that's whole now. The newest note says what changed."""
    from decompnet.consoles.nds import recover, scratch

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,description,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)",
                         ("ds", "DS", "", "arm", 0, str(tmp_path), json.dumps({"kind": "fork"}))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(?,?,?,?,?,?)",
                   (pid, "f", 16, 4, "open", "a"))
        db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit) VALUES(?,?,?,?,?,?)",
                   (pid, "e", 16, 4, "open", "a"))
    fp = type("FP", (), {"manifest": {"created": "from scratch", "analysis": 5, "version": "v"}, "root": tmp_path})()
    monkeypatch.setattr(net, "fork_slug", lambda slug: slug)
    monkeypatch.setattr(net.forks, "get", lambda slug: (None, fp, None))
    monkeypatch.setattr(net.forks, "reanalyse", lambda slug, step, **kw: step(fp))
    monkeypatch.setattr(recover, "move_boundaries", lambda *a, **kw: [])
    monkeypatch.setattr(recover, "merge_pieces", lambda *a, **kw: {"f": ["g", "h"]})
    monkeypatch.setattr(recover, "joined_pieces", lambda *a: {"e": ["e2"]})  # joined by the version before
    monkeypatch.setattr(scratch, "dsd_path", lambda: "dsd")
    consoles.get("nds").on_start(net, "ds", log=lambda *_: None)
    note = net.store.one("SELECT text FROM notes WHERE function_id=(SELECT id FROM functions WHERE name='f')")["text"]
    assert "joined g, h" in note and "whole function now" in note
    assert "joined e2" in net.store.one("SELECT text FROM notes WHERE function_id=(SELECT id FROM functions WHERE "
                                        "name='e')")["text"]


def test_a_function_branching_into_the_middle_of_the_next_one_takes_it(tmp_path, monkeypatch):
    """A switch whose cases ds-decomp cut into the symbols after it: compiled code branches only within itself or
    to another function's start, so a branch into the middle of the next symbol makes that symbol part of it."""
    from decompnet.consoles.nds import recover

    words = [0xE92D4010, 0xEA000002, 0xE8BD8010,  # f: push {r4, lr}; b g+8; pop {r4, pc}
             0xE52DE004, 0xE3A00001, 0xE3A00002, 0xE49DF004]  # g: push {lr}; mov r0, #1; mov r0, #2; pop {pc}
    folder = tmp_path / "arm9"
    folder.mkdir()
    lines = [f"f kind:function(arm,size=0xc) addr:{BASE:#010x}", f"g kind:function(arm,size=0x10) addr:{BASE + 0xC:#010x}"]
    (folder / "symbols.txt").write_text("\n".join(lines) + "\n")
    (folder / "delinks.txt").write_text(f"    .text start:{BASE:#010x} end:{BASE + 0x1C:#010x} kind:code\n\n"
                                        f"src/a.c:\n    .text start:{BASE:#010x} end:{BASE + 0x1C:#010x}\n")
    m = recover.Module("main", "main", folder, BASE, b"".join(w.to_bytes(4, "little") for w in words),
                       [(BASE, BASE + 0x1C)], symbol_lines=lines)
    m.functions = {BASE: (0xC, False), BASE + 0xC: (0x10, False)}
    monkeypatch.setattr(recover, "load_modules", lambda config, extract: [m])
    assert recover.merge_pieces(tmp_path, "v1", log=lambda *_: None) == {"f": ["g"]}
    assert (folder / "symbols.txt").read_text().splitlines()[0] == f"f kind:function(arm,size=0x1c) addr:{BASE:#010x}"


def test_a_switch_running_on_into_the_next_source_file_takes_its_function_along(tmp_path, monkeypatch):
    """A DS game's 3 KB function was cut at a source file's end: its switch's cases were in the next file's first
    symbol, out of agents' reach. That function moves to the file before (the next file keeps the rest), unless C in
    its file defines it or it's matched."""
    from decompnet.consoles.nds import recover

    words = [0xE92D4010, 0xEA000002, 0xE8BD8010,  # f: push {r4, lr}; b g+8; pop {r4, pc} (the end of a.c)
             0xE52DE004, 0xE3A00001, 0xE3A00002, 0xE49DF004,  # g: the first function of b.c, f's cases
             0xE12FFF1E]  # h: bx lr, b.c's next function
    folder = tmp_path / "config" / "v1" / "arm9"
    folder.mkdir(parents=True)
    lines = [f"f kind:function(arm,size=0xc) addr:{BASE:#010x}", f"g kind:function(arm,size=0x10) addr:{BASE + 0xC:#010x}",
             f"h kind:function(arm,size=0x4) addr:{BASE + 0x1C:#010x}"]
    delinks = (f"    .text start:{BASE:#010x} end:{BASE + 0x20:#010x} kind:code\n\n"
               f"src/a.c:\n    .text start:{BASE:#010x} end:{BASE + 0xC:#010x}\n\n"
               f"src/b.c:\n    .text start:{BASE + 0xC:#010x} end:{BASE + 0x20:#010x}\n")
    (folder / "delinks.txt").write_text(delinks)
    m = recover.Module("main", "main", folder, BASE, b"".join(w.to_bytes(4, "little") for w in words),
                       [(BASE, BASE + 0x20)], symbol_lines=lines)
    m.functions = {BASE: (0xC, False), BASE + 0xC: (0x10, False), BASE + 0x1C: (4, False)}
    monkeypatch.setattr(recover, "load_modules", lambda config, extract: [m])
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.c").write_text("void g(void) {\n}\n")
    assert recover.move_boundaries(tmp_path, "v1", log=lambda *_: None) == []  # agents' C for g is in b.c
    (tmp_path / "src" / "b.c").write_text("extern void g(void);\n")
    assert recover.move_boundaries(tmp_path, "v1", keep={"g"}, log=lambda *_: None) == []  # matched
    assert recover.move_boundaries(tmp_path, "v1", log=lambda *_: None) == ["g"]
    text = (folder / "delinks.txt").read_text()
    assert f"src/a.c:\n    .text start:{BASE:#010x} end:{BASE + 0x1C:#010x}" in text
    assert f"src/b.c:\n    .text start:{BASE + 0x1C:#010x} end:{BASE + 0x20:#010x}" in text


def test_two_pieces_with_bytes_no_symbol_claims_between_them_are_joined_with_those_bytes(tmp_path, monkeypatch):
    """On a DS game a function's two pieces had 68 bytes between them that ds-decomp's analysis gave no symbol: they
    were never joined, as they weren't next to each other. Bytes no symbol claims go with the join; any symbol
    between them stops it."""
    from decompnet.consoles.nds import recover

    words = [0xE92D4010, 0xE1A04000,  # f: push {r4, lr}; mov r4, r0 (runs on)
             0xE1A00004,  # no symbol: mov r0, r4
             0xE8BD8010]  # g: pop {r4, pc}
    folder = tmp_path / "arm9"
    folder.mkdir()
    lines = [f"f kind:function(arm,size=0x8) addr:{BASE:#010x}", f"g kind:function(arm,size=0x4) addr:{BASE + 0xC:#010x}"]
    (folder / "symbols.txt").write_text("\n".join(lines) + "\n")
    (folder / "delinks.txt").write_text(f"    .text start:{BASE:#010x} end:{BASE + 0x10:#010x} kind:code\n\n"
                                        f"src/a.c:\n    .text start:{BASE:#010x} end:{BASE + 0x10:#010x}\n")
    m = recover.Module("main", "main", folder, BASE, b"".join(w.to_bytes(4, "little") for w in words),
                       [(BASE, BASE + 0x10)], symbol_lines=lines, symbols={BASE, BASE + 0xC})
    m.functions = {BASE: (8, False), BASE + 0xC: (4, False)}
    monkeypatch.setattr(recover, "load_modules", lambda config, extract: [m])
    assert recover.merge_pieces(tmp_path, "v1", log=lambda *_: None) == {"f": ["g"]}
    assert (folder / "symbols.txt").read_text().splitlines()[0] == f"f kind:function(arm,size=0x10) addr:{BASE:#010x}"
    (folder / "symbols.txt").write_text("\n".join(lines) + "\n")
    m.symbols.add(BASE + 8)  # a symbol between them: not joined
    assert recover.merge_pieces(tmp_path, "v1", log=lambda *_: None) == {}


def _two_files(tmp_path, monkeypatch, words, lines, files):
    """A main program of `words` at BASE with symbols.txt `lines` and source files `files` [(path, start, end)]."""
    from decompnet.consoles.nds import recover

    folder = tmp_path / "config" / "v1" / "arm9"
    folder.mkdir(parents=True, exist_ok=True)
    end = BASE + 4 * len(words)
    (folder / "symbols.txt").write_text("\n".join(lines) + "\n")
    (folder / "delinks.txt").write_text(f"    .text start:{BASE:#010x} end:{end:#010x} kind:code\n\n" + "\n\n".join(
        f"{p}:\n    .text start:{a:#010x} end:{b:#010x}\n" for p, a, b in files))
    m = recover.Module("main", "main", folder, BASE, b"".join(w.to_bytes(4, "little") for w in words), [(BASE, end)],
                       symbol_lines=lines)
    for ln in lines:
        s = recover.SYMBOL.match(ln)
        m.symbols.add(int(s.group(4), 16))
        if s.group(2) == "function":
            m.functions[int(s.group(4), 16)] = (int(re.search(r"size=(0x[0-9a-f]+)", ln).group(1), 16), False)
    monkeypatch.setattr(recover, "load_modules", lambda config, extract: [m])
    return folder


def test_agents_join_two_functions_the_analysis_cut_and_check_again_keeps_it(tmp_path, monkeypatch):
    """join_functions: what agents agreed the analysis cut becomes one function, the symbols between it labels, across
    two source files too; never a matched function or data. Check again's own joining only joins, never splits:
    what the agents joined stays."""
    from decompnet.consoles.nds import recover
    from decompnet.forks.project import ForkError

    words = [0xE92D4010, 0xE1A04000, 0xE8BD8010, 0xE3A00001, 0xE12FFF1E, 0xE12FFF1E]  # f (12), g (8), h (4)
    lines = [f"f kind:function(arm,size=0xc) addr:{BASE:#010x}", f"g kind:function(arm,size=0x8) addr:{BASE + 0xC:#010x}",
             f"h kind:function(arm,size=0x4) addr:{BASE + 0x14:#010x}"]
    folder = _two_files(tmp_path, monkeypatch, words, lines, [("src/a.c", BASE, BASE + 0xC), ("src/b.c", BASE + 0xC, BASE + 0x18)])
    with pytest.raises(ForkError, match="matched"):
        recover.join_functions(tmp_path, "v1", "f", "g", keep={"g"})
    assert recover.join_functions(tmp_path, "v1", "g", "f") == "g joined to f (moved from src/b.c)"
    assert (folder / "symbols.txt").read_text().splitlines()[:2] == [
        f"f kind:function(arm,size=0x14) addr:{BASE:#010x}", f"g kind:label(arm) addr:{BASE + 0xC:#010x}"]
    text = (folder / "delinks.txt").read_text()
    assert f"src/a.c:\n    .text start:{BASE:#010x} end:{BASE + 0x14:#010x}" in text
    # Check again afterwards: nothing it does takes the join apart
    joined = (folder / "symbols.txt").read_text()
    m2 = recover.Module("main", "main", folder, BASE, b"".join(w.to_bytes(4, "little") for w in words), [(BASE, BASE + 0x18)],
                        symbol_lines=joined.splitlines(), symbols={BASE, BASE + 0xC, BASE + 0x14})
    m2.functions = {BASE: (0x14, False), BASE + 0x14: (4, False)}
    monkeypatch.setattr(recover, "load_modules", lambda config, extract: [m2])
    recover.move_boundaries(tmp_path, "v1", log=lambda *_: None)
    recover.merge_pieces(tmp_path, "v1", log=lambda *_: None)
    assert (folder / "symbols.txt").read_text().splitlines()[0] == f"f kind:function(arm,size=0x14) addr:{BASE:#010x}"
    assert "kind:label" in (folder / "symbols.txt").read_text().splitlines()[1]


def test_a_join_needs_agents_of_two_models_and_a_refused_one_isnt_tried_again(tmp_path, monkeypatch):
    """Three agents of two models propose the same join: it's tried, checked by the rebuild (reanalyse). One that
    doesn't hold is remembered, and the next proposal is told so instead of trying again."""
    from decompnet.app import forkengine

    net = RomNetwork(tmp_path / "app.db", tmp_path / "ws")
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "platform": "nds", "ready": True}, "x")
    with net.store.tx() as db:
        for k, name in enumerate(("f", "g")):
            db.execute("INSERT INTO functions(project_id,name,size,n_insns,status,unit,vram) VALUES(1,?,8,2,'open','a',?)",
                       (name, BASE + 8 * k))
    tried = []
    monkeypatch.setattr(forkengine.ForkEngine, "reanalyse", lambda self, slug, step, **kw: tried.append(step) or None)
    say = lambda who: net.forks.propose_join(net.local_agent(who)[0], "p", "g", "f", "f has no epilogue; g pops it")  # noqa: E731
    assert "1 of 3 agents" in say("opencode-beam-free-1")
    assert "1 of 2 models" in say("opencode-beam-free-2") and not tried
    assert "refused" in say("claude-sonnet-5-5-1") and len(tried) == 1  # tried, and the rebuild took it back
    assert "was tried, and refused" in say("claude-opus-5-5-1") and len(tried) == 1
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched' WHERE name='g'")
    with pytest.raises(NetworkError, match="matched"):
        say("claude-opus-5-5-2")


def test_a_ds_source_file_is_split_in_its_delinks_with_its_compiler(tmp_path):
    """split_file on DS: agents wanted a function built by another CodeWarrior version in a file of its own, to give
    it its own compiler (try_compiler), and split_file was GameCube/Wii's only. The file's block in delinks.txt ends
    at the function, the new file's follows, and the new file has the old one's compiler."""
    folder = tmp_path / "config" / "v1" / "arm9"
    folder.mkdir(parents=True)
    (folder / "symbols.txt").write_text(f"f kind:function(arm,size=0x8) addr:{BASE:#010x}\n"
                                        f"g kind:function(arm,size=0x8) addr:{BASE + 8:#010x}\n")
    (folder / "delinks.txt").write_text(f"    .text start:{BASE:#010x} end:{BASE + 0x20:#010x} kind:code\n\n"
                                        f"src/a.c:\n    .text start:{BASE:#010x} end:{BASE + 0x10:#010x}\n\n"
                                        f"src/b.c:\n    .text start:{BASE + 0x10:#010x} end:{BASE + 0x20:#010x}\n")
    (folder / "file_compilers.json").write_text(json.dumps({"src/a.c": "1.2/sp4"}))
    fp = type("FP", (), {"root": tmp_path})()
    saved = consoles.get("nds").split_source(fp, "v1", "src/a.c", "g", "src/a_g.c")
    assert set(saved) == {folder / "delinks.txt", folder / "file_compilers.json"}
    assert (f"src/a.c:\n    .text start:{BASE:#010x} end:{BASE + 8:#010x}\n\n"
            f"src/a_g.c:\n    .text start:{BASE + 8:#010x} end:{BASE + 0x10:#010x}\n\n"
            f"src/b.c:") in (folder / "delinks.txt").read_text()
    assert json.loads((folder / "file_compilers.json").read_text())["src/a_g.c"] == "1.2/sp4"


def test_a_split_takes_every_declaration_with_the_functions_it_moves(tmp_path):
    """The moved C used data declared just before it, far below the file's top: the new file failed to compile. It
    gets every declaration of the old file, wherever it stands, and the old file keeps its own."""
    from decompnet.app.forkengine import ForkEngine

    text = ('#include "common.h"\n\nvoid f(void) {\n}\n\nextern int data_x;\n\n'
            'int g(void) {\n    return data_x;\n}\n')
    rows = [{"name": n, "meta": json.dumps({"symbol": n}), "status": "open"} for n in ("f", "g")]
    eng = ForkEngine.__new__(ForkEngine)
    new, old, moved = eng._divide(text, rows, {"g"})
    assert moved == 1 and "extern int data_x;" in new and "int g(void)" in new and "void f(void)" not in new
    assert "void f(void)" in old and "int g(void)" not in old and "extern int data_x;" in old


def test_an_address_outside_a_modules_code_is_no_function_start(tmp_path):
    """A pointer whose value lies in another module's range (overlays share addresses) was checked against this
    module's bytes: below its start the offset was negative, passed the length check and indexed out of range, and
    a user's Check again failed with an IndexError."""
    from decompnet.consoles.nds import recover

    m = recover.Module("ov000", "overlay(0)", tmp_path, BASE, (0xE12FFF1E).to_bytes(4, "little"), [(BASE, BASE + 4)])
    assert recover._starts_like_code(m, BASE, False)
    for addr in (BASE - 0x1000, BASE - 8, BASE + 4, BASE + 0x1000):
        assert not recover._starts_like_code(m, addr, False) and not recover._starts_like_code(m, addr, True)
