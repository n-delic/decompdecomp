"""Own x64 compilers through the PC importer, normal judge and rebuild check.

The executable is made here from small original C functions; no game data or compiler binaries are fixtures.
These tests skip unless Setup has an x64 Visual C++ copy, Ghidra and objdiff (and wine off Windows).
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import kits, toolchains, wine
from decompnet.app.engine import RomNetwork
from decompnet.consoles.pc import scratch
from decompnet.forks import ghidra
from decompnet.limits import capped

FUNCTIONS = {
    "add": "__declspec(noinline) int add(int x, int y) { return x + y; }",
    "total": "__declspec(noinline) int total(int n) { int i, s = 0; for (i = 0; i < n; i++) s += add(values[i & 3], i); return s; }",
    "start": "int start(void) { return total(4); }",
}
COPIES = [c for c in kits.compilers(("visualcpp",)) if c.version.endswith("-x64")]
READY = ghidra.available() and toolchains.installed("objdiff-cli") is not None and (
    sys.platform == "win32" or wine.find() is not None)


def _build(copy, folder):
    """Use this copy's own linker too, so the Rich header identifies the compiler we will select."""
    folder.mkdir()
    source = "int values[4] = {3, 5, 7, 11};\n" + "\n".join(FUNCTIONS.values())
    (folder / "fixture.c").write_text(source)
    link = kits.find(copy.exe.parent, "link.exe")
    if link is None:
        pytest.skip("the compiler copy has no linker beside cl.exe")
    env = os.environ.copy()
    run = []
    if sys.platform != "win32":
        env.update(wine.environment(wine.prepare(log=lambda *_: None)))
        run = [wine.find()]
    for argv in (
        [str(copy.exe), "/nologo", "/c", "/O2", "fixture.c"],
        [str(link), "/nologo", "/NODEFAULTLIB", "/ENTRY:start", "/SUBSYSTEM:CONSOLE",
         "/FIXED", "/OPT:NOREF", "/MACHINE:X64", "/MAP:symbols.map", "/OUT:fixture.exe", "fixture.obj"],
    ):
        done = subprocess.run(capped([*run, *argv]), cwd=folder, env=env, capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr
    symbols = {m.group(1): int(m.group(2), 16) for m in re.finditer(
        r"^\s+\S+\s+(\S+)\s+([0-9a-fA-F]{8,16}) ", (folder / "symbols.map").read_text(), re.M)}
    assert set(FUNCTIONS) | {"values"} <= symbols.keys()
    return folder / "fixture.exe", symbols


@pytest.mark.skipif(not READY, reason="needs Ghidra, objdiff and, off Windows, wine")
@pytest.mark.parametrize("copy", COPIES or [None], ids=[c.version for c in COPIES] or ["no-own-x64"])
def test_own_x64_compiler_imports_matches_and_rebuilds(copy, tmp_path):
    if copy is None:
        pytest.skip("needs a user-supplied x64 Visual C++ copy")
    exe, symbols = _build(copy, tmp_path / "original")
    info = scratch.game_info(exe)
    assert info["target"] == "x64" and info["compiler"] == copy.version
    net = RomNetwork(tmp_path / "app.db", tmp_path / "projects")
    logs = []
    slug = net.import_rom(exe, confirmed=True, log=logs.append)
    project = net.project_row(slug)
    root = Path(project["root"])
    assert copy.version in (root / "tools/configure.py").read_text()
    rows = net.store.all("SELECT name FROM functions WHERE project_id=?", project["id"])
    names = {name: f"func_{symbols[name]:08X}" for name in FUNCTIONS}
    assert {r["name"] for r in rows} == set(names.values())
    assert net.overview(slug)["rebuild"]["identical"]

    agent, _ = net.local_agent("own-x64-test")
    renamed = {**names, "values": f"D_{symbols['values']:08X}"}

    def sub(source):
        for old, new in renamed.items():
            source = re.sub(r"\b" + old + r"\b", new, source)
        return source

    header = "extern int values[4];\nint add(int, int);\nint total(int);\n"
    assert net.forks.edit_file(agent, slug, "include/common.h", "", sub(header), "fixture declarations").startswith("✅")
    wrong = net.attempt(agent, names["add"], sub(FUNCTIONS["add"].replace("x + y", "x - y")), slug)
    assert not wrong["matched"], wrong
    for name in sorted(FUNCTIONS, key=symbols.get):
        result = net.attempt(agent, names[name], sub(FUNCTIONS[name]), slug)
        assert result["matched"], result["text"]
    assert not net.store.all("SELECT name FROM functions WHERE project_id=? AND status != 'matched'", project["id"])
    assert net.forks.check_rom(slug, log=lambda *_: None)["identical"]

    polished = "// Adds the two inputs.\n" + sub(FUNCTIONS["add"]) + "\n"
    assert "readable now" in net.readability.submit(agent, slug, names["add"], polished)
    assert net.forks.check_rom(slug, log=lambda *_: None)["identical"]
    result = net.forks.reimport(slug, root / "orig/fixture.exe", log=lambda *_: None)
    assert result["said"].startswith("the new analysis changed nothing"), result
    assert not net.store.all("SELECT name FROM functions WHERE project_id=? AND status != 'matched'", project["id"])
