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
    """Sonic & SEGA All-Stars Racing calls 0x0206135C, which dsd's analysis didn't take for a function ("Local
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
    dsd = tmp_path / "dsd"  # what `dsd json delinks` says, for a project with nothing delinked yet
    dsd.write_text(f"#!{sys.executable}\nimport json\nprint(json.dumps({{'files': [], 'arm9_lcf_file': 'build/arm9.lcf', "
                   "'arm9_objects_file': 'build/objects.txt'}))\n")
    dsd.chmod(0o755)
    subprocess.run([sys.executable, "tools/configure.py", "adqe", "--dsd", str(dsd), "--compilers", str(compilers)],
                   cwd=root, check=True, capture_output=True)
    ninja = re.sub(r"\$\n\s*", "", (root / "build.ninja").read_text())  # (ninja_syntax wraps long lines)

    def compiler_of(name: str) -> str:
        block = ninja.split(f"build build/adqe/src/auto/{name}.o: mwcc", 1)[1].split("\nbuild ", 1)[0]
        return block.split("cc = ", 1)[1].split("\n", 1)[0]
    assert compiler_of("main_02000000").endswith("2.0/sp1p5/mwccarm.exe")
    assert compiler_of("main_02000400").endswith("1.2/sp4/mwccarm.exe")
    assert "config/adqe/arm9/file_compilers.json" in ninja.split("build build.ninja: configure", 1)[1].split("\n", 1)[0]
