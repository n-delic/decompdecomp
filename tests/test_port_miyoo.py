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


def test_the_pc_build_never_compiles_the_miyoo_host():
    """The kit's PC and browser builds compile the kit folder's *.c: the Miyoo's files sit in miyoo/, out of that."""
    assert not (KIT / "miyoo.c").exists() and not (KIT / "mem32.c").exists()
    assert (KIT / "miyoo" / "miyoo.c").is_file() and (KIT / "miyoo" / "mem32.c").is_file()


def test_the_miyoo_host_compiles_for_32_bit_arm(tmp_path):
    """mem32.c and miyoo.c built as the export builds them (the pinned Zig, glibc 2.28, Cortex-A7)."""
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
