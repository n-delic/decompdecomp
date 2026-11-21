"""The Miyoo Mini export (port/wasm/miyoo.py): the native build's game, its host compiled for the Miyoo Mini's 32-bit
ARM chip, packed as folders for its SD card."""

import os
import subprocess
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.port.wasm import export, miyoo

KIT = Path(__file__).parents[1] / "src" / "decompnet" / "consoles" / "n64" / "port_wasm"


def test_the_miyoo_mini_is_an_export_kind():
    assert "miyoo" in export.KINDS


def test_the_pack_has_an_app_for_the_stock_os_and_onion_and_a_pak_for_minui(tmp_path):
    program = tmp_path / "game"
    program.write_bytes(b"\x7fELF")
    dest = miyoo.package(program, tmp_path / "out", "RALLY 64", "RALLY 64 Europe")
    files = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert files == ["App/Rally64/config.json", "App/Rally64/game", "App/Rally64/launch.sh", "README.txt",
                     "Tools/miyoomini/RALLY 64.pak/launch.sh"]
    launch = (dest / "App" / "Rally64" / "launch.sh").read_text()
    assert os.access(dest / "App" / "Rally64" / "launch.sh", os.X_OK) and os.access(dest / "App" / "Rally64" / "game", os.X_OK)
    assert "rom.z64" in launch and 'PORT_SAVE="$DIR/game.eep"' in launch and "exec ./game" in launch
    assert "PORT_ROTATE=0" in (dest / "Tools" / "miyoomini" / "RALLY 64.pak" / "launch.sh").read_text()
    assert '"launch": "launch.sh"' in (dest / "App" / "Rally64" / "config.json").read_text()
    assert "RALLY 64 Europe" in (dest / "README.txt").read_text()
    # exporting again replaces the folder, so nothing stale is copied to the card
    (dest / "stale").write_text("x")
    assert not (miyoo.package(program, tmp_path / "out", "RALLY 64") / "stale").exists()


def test_the_projects_rom_goes_in_the_app_folder_ready_for_the_card(tmp_path):
    """The project has the user's ROM: the pack carries it as App/<Game>/rom.z64, and the steps say so."""
    program, rom = tmp_path / "game", tmp_path / "rom.z64"
    program.write_bytes(b"\x7fELF")
    rom.write_bytes(b"\x80\x37\x12\x40" + bytes(60))
    dest = miyoo.package(program, tmp_path / "out", "RALLY 64", "RALLY 64 Europe", rom)
    assert (dest / "App" / "Rally64" / "rom.z64").read_bytes() == rom.read_bytes()
    assert "already in App/Rally64/" in (dest / "README.txt").read_text()
    steps = miyoo.instructions(dest, "RALLY 64")
    assert "SD card" in steps[0] and str(dest) in steps[0]
    assert steps[1] == "Your ROM is already in App/Rally64/ as rom.z64."
    # without one (a pack made elsewhere), the steps ask for it
    assert "as rom.z64" in miyoo.instructions(miyoo.package(program, tmp_path / "bare", "RALLY 64"), "RALLY 64")[1]
    assert "Put your ROM" in (tmp_path / "bare" / "README.txt").read_text()


def test_the_pc_build_never_compiles_the_miyoo_host():
    """The kit's PC and browser builds compile the kit folder's *.c: the Miyoo's files sit in miyoo/, out of that."""
    assert not (KIT / "miyoo.c").exists() and not (KIT / "mem32.c").exists()
    assert (KIT / "miyoo" / "miyoo.c").is_file() and (KIT / "miyoo" / "mem32.c").is_file()


def test_the_miyoo_host_compiles_for_32_bit_arm(tmp_path):
    """mem32.c and miyoo.c built as the export builds them (the pinned Zig, glibc 2.28, Cortex-A7)."""
    if toolchains.installed("zig") is None:
        pytest.skip("needs the pinned Zig and WABT")
    zig, wabt = toolchains.binary("zig"), toolchains.root() / "wabt"
    if not zig.is_file() or not (wabt / "include" / "wasm-rt.h").is_file():
        pytest.skip("needs the pinned Zig and WABT")
    rt = wabt / "share" / "wabt" / "wasm2c"
    env = {**os.environ, "ZIG_GLOBAL_CACHE_DIR": str(tmp_path / "zc"), "ZIG_LOCAL_CACHE_DIR": str(tmp_path / "zc")}
    for unit in ("mem32.c", "miyoo.c"):
        done = subprocess.run([str(zig), "cc", *miyoo.TARGET, *miyoo.FLAGS, "-Werror", "-Wall", "-Wno-everything",
                               "-I", str(wabt / "include"), "-I", str(rt), "-c", str(KIT / "miyoo" / unit),
                               "-o", str(tmp_path / (unit + ".o"))], capture_output=True, text=True, env=env, timeout=300)
        assert done.returncode == 0, done.stderr


def test_export_packs_the_projects_rom_and_returns_the_steps(tmp_path, monkeypatch):
    """export() for "miyoo": the project's ROM (play.game's third value) lands in the pack, and the result carries
    the steps the Porting tab shows under the log."""
    from decompnet.port.wasm import play
    repo, rom, program = tmp_path / "save", tmp_path / "rom.z64", tmp_path / "game"
    repo.mkdir()
    rom.write_bytes(b"\x80\x37\x12\x40" + bytes(60))
    program.write_bytes(b"\x7fELF")
    cfg = {"platform": "n64", "rom": {"title": "RALLY 64", "region": "Europe"}}
    monkeypatch.setattr(play, "game", lambda net, slug: (repo, cfg, rom))
    monkeypatch.setattr(play, "library", lambda net, slug, log=print: set())
    monkeypatch.setattr(play, "unverified", lambda net, slug, log=print: set())  # (the save is whole)
    monkeypatch.setattr(play, "library_c", lambda net, slug: {})  # (no ultralib units: port/ultralib.py)
    monkeypatch.setattr(play, "library_names", lambda net, slug: {})  # (no library names to rename)
    monkeypatch.setattr(play, "recompiled", lambda net, slug, log=print: None)  # (no recompiled C)
    monkeypatch.setattr(play, "function_headers", lambda net, slug: {})  # (headers as the save names them)
    monkeypatch.setattr(miyoo, "build", lambda *a, **k: program)
    result = export.export(None, "rally", "miyoo", log=lambda *a: None)
    assert "App/Rally64/rom.z64" in result["files"]
    assert any("SD card" in step for step in result["instructions"])


def test_the_miyoo_export_runs_recompiled_c_as_the_app_does(tmp_path, monkeypatch):
    """The Miyoo Mini's chip interprets MIPS several times slower than a PC, and the browser build of a racing game
    reached 14 fps on a PC with 91 functions interpreted: the pack gets the recompiled C the app export has."""
    from decompnet.port.wasm import play
    repo, rom, program = tmp_path / "save", tmp_path / "rom.z64", tmp_path / "game"
    repo.mkdir()
    rom.write_bytes(b"\x80\x37\x12\x40" + bytes(60))
    program.write_bytes(b"\x7fELF")
    recomp = {"folder": str(tmp_path / "recomp"), "header": str(tmp_path / "recomp.h")}
    monkeypatch.setattr(play, "game", lambda net, slug: (repo, {"platform": "n64", "rom": {"title": "RALLY 64"}}, rom))
    monkeypatch.setattr(play, "inputs", lambda net, slug, log=print: {
        "leave_out": set(), "library_c": {}, "library_names": {}, "recomp": recomp, "function_headers": {}})
    took = {}
    monkeypatch.setattr(miyoo, "build", lambda *a, **k: took.update(k) or program)
    export.export(None, "rally", "miyoo", log=lambda *a: None)
    assert took["recomp"] == recomp


def test_the_miyoo_build_compiles_the_recompiled_c_only_when_it_has_it(tmp_path, monkeypatch):
    """The native build leaves the functions recompiled C bridges out of interp.c, so the Miyoo's host must compile
    the same units; out/recomp stays behind from an earlier build, so without `recomp` none of it goes in."""
    from decompnet import toolchains
    from decompnet.port.wasm import build as native
    repo = tmp_path / "save"
    out = repo / "port" / "wasm" / "build"
    (out / "recomp").mkdir(parents=True)
    for name in ("rec_funcs_0.c", "rec_funcs_1.c", "bridges.c"):
        (out / "recomp" / name).write_text("")
    folder = tmp_path / "recomp"
    folder.mkdir()
    (folder / "recomp.json").write_text("{}")
    took = {}
    monkeypatch.setattr(native, "build", lambda *a, **k: took.update(k))
    monkeypatch.setattr(toolchains, "binary", lambda name: tmp_path / name)
    monkeypatch.setattr(toolchains, "ensure", lambda name, log=print: tmp_path / name)
    compiled = []
    monkeypatch.setattr(native, "_parallel", lambda units, work, log, what, step: compiled.append(list(units)) or [])
    monkeypatch.setattr(native, "_run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    (out / "miyoo").mkdir()
    (out / "miyoo" / "game").write_bytes(b"\x7fELF")  # (what the link would have made)
    recomp = {"folder": str(folder), "header": str(tmp_path / "recomp.h")}
    miyoo.build(repo, log=lambda *a: None, recomp=recomp)
    assert took["recomp"] == recomp
    names = {u.name for u in compiled[-1] if u.parent == out / "recomp"}
    assert names == {"rec_funcs_0.c", "rec_funcs_1.c", "bridges.c"}
    miyoo.build(repo, log=lambda *a: None)
    assert not any(u.parent == out / "recomp" for u in compiled[-1])


def test_mem32_maps_every_stack_recompiled_c_runs_on():
    """recomp.py's MIPS stacks sit at 0x14000000, one per host thread: on a 32-bit chip only what mem32.c maps
    exists, so recompiled code's first call faulted where they weren't mapped."""
    import re

    from decompnet.port.wasm import recomp
    text = (KIT / "miyoo" / "mem32.c").read_text()
    regions = [(int(a, 16), int(b, 16)) for a, b in re.findall(r"\{0x([0-9A-Fa-f]+)u, 0x([0-9A-Fa-f]+)u, \"", text)]
    # port_recomp_thread: thread n's stack ends at STACKS + (n + 2) * STACK_SIZE, from the boot's (-1) to the 31st
    low, high = recomp.STACKS + recomp.STACK_SIZE, recomp.STACKS + 32 * recomp.STACK_SIZE
    assert any(start <= low and high <= start + size for start, size in regions)
