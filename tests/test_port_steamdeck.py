"""The Steam Deck export (port/wasm/steamdeck.py): the native build's game, its host compiled for 64-bit x86 Linux
with its own full-screen window, packed as a folder to add to Steam as a non-Steam game."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.port.wasm import export, steamdeck

KIT = Path(__file__).parents[1] / "src" / "decompnet" / "consoles" / "n64" / "port_wasm"


def test_the_steam_deck_is_an_export_kind():
    assert "steamdeck" in export.KINDS


def test_the_pack_is_a_folder_with_the_program_and_what_steam_starts(tmp_path):
    program = tmp_path / "game"
    program.write_bytes(b"\x7fELF")
    dest = steamdeck.package(program, tmp_path / "out", "RALLY 64", "RALLY 64 Europe")
    files = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert files == ["README.txt", "Rally64/game", "Rally64/launch.sh"]
    launch = (dest / "Rally64" / "launch.sh").read_text()
    assert os.access(dest / "Rally64" / "launch.sh", os.X_OK) and os.access(dest / "Rally64" / "game", os.X_OK)
    assert launch.startswith("#!/bin/bash\n")
    # its own window, full screen, its saves beside it, until the player quits
    assert "PORT_WINDOW=1" in launch and 'PORT_FULLSCREEN="${PORT_FULLSCREEN:-1}"' in launch
    assert 'PORT_SAVE="$DIR/game.eep"' in launch and './game "$ROM" 0' in launch
    # SDL3 from the Steam client the Deck already has, when the system has none
    assert "ubuntu12_64/libSDL3.so.0" in launch and "export PORT_SDL=" in launch
    readme = (dest / "README.txt").read_text()
    assert "RALLY 64 Europe" in readme and "Add a Non-Steam Game" in readme
    # exporting again replaces the folder, so nothing stale is copied to the Deck
    (dest / "stale").write_text("x")
    assert not (steamdeck.package(program, tmp_path / "out", "RALLY 64") / "stale").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_launcher_is_valid_bash_even_for_a_title_with_quotes(tmp_path):
    program = tmp_path / "game"
    program.write_bytes(b"\x7fELF")
    dest = steamdeck.package(program, tmp_path / "out", 'Rally "64" $HOME `x`')
    launch = next(dest.glob("*/launch.sh"))
    done = subprocess.run(["bash", "-n", str(launch)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "$HOME`" not in launch.read_text() and '"64"' not in launch.read_text()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_launcher_without_a_rom_says_so_in_its_log(tmp_path):
    program = tmp_path / "game"
    program.write_text("#!/bin/sh\nexit 9\n")
    dest = steamdeck.package(program, tmp_path / "out", "RALLY 64")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}  # (no kdialog or zenity to show it)
    done = subprocess.run(["bash", str(dest / "Rally64" / "launch.sh")], capture_output=True, text=True, env=env)
    assert done.returncode == 1
    assert "No ROM" in (dest / "Rally64" / "play.log").read_text()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_launcher_finds_the_steam_clients_sdl3_and_starts_the_game_with_the_rom(tmp_path):
    program = tmp_path / "game"
    program.write_text('#!/bin/sh\necho "args=$1 $2 window=$PORT_WINDOW full=$PORT_FULLSCREEN sdl=$PORT_SDL"\n')
    rom = tmp_path / "rom.z64"
    rom.write_bytes(b"\x80\x37\x12\x40" + bytes(60))
    sdl = tmp_path / ".local" / "share" / "Steam" / "ubuntu12_64" / "libSDL3.so.0"
    sdl.parent.mkdir(parents=True)
    sdl.write_bytes(b"")
    dest = steamdeck.package(program, tmp_path / "out", "RALLY 64", rom_file=rom)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    done = subprocess.run(["bash", str(dest / "Rally64" / "launch.sh")], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    log = (dest / "Rally64" / "play.log").read_text()
    assert f"args=rom.z64 0 window=1 full=1 sdl={sdl}" in log


def test_the_projects_rom_goes_in_the_folder_and_the_steps_say_so(tmp_path):
    program, rom = tmp_path / "game", tmp_path / "rom.z64"
    program.write_bytes(b"\x7fELF")
    rom.write_bytes(b"\x80\x37\x12\x40" + bytes(60))
    dest = steamdeck.package(program, tmp_path / "out", "RALLY 64", "RALLY 64 Europe", rom)
    assert (dest / "Rally64" / "rom.z64").read_bytes() == rom.read_bytes()
    assert "already in Rally64/" in (dest / "README.txt").read_text()
    steps = steamdeck.instructions(dest, "RALLY 64")
    assert "Steam Deck" in steps[0] and str(dest) in steps[0]
    assert steps[1] == "Your ROM is already in Rally64/ as rom.z64."
    assert "Non-Steam Game" in steps[2] and "launch.sh" in steps[2]
    bare = steamdeck.package(program, tmp_path / "bare", "RALLY 64")
    assert "as rom.z64" in steamdeck.instructions(bare, "RALLY 64")[1]
    assert "Put your ROM" in (bare / "README.txt").read_text()


def test_export_packs_the_projects_rom_and_returns_the_steps(tmp_path, monkeypatch):
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
    monkeypatch.setattr(steamdeck, "build", lambda *a, **k: took.update(k) or program)
    result = export.export(None, "rally", "steamdeck", log=lambda *a: None)
    assert "Rally64/rom.z64" in result["files"] and "Rally64/launch.sh" in result["files"]
    assert any("Non-Steam Game" in step for step in result["instructions"])
    assert took["recomp"] == recomp  # the recompiled C runs on the Deck, as in the app export


def test_the_steam_deck_build_compiles_the_native_hosts_units_with_its_window(tmp_path, monkeypatch):
    """The PC's own host (live.c, wasm2c's 64-bit memory, window/sdl.c), not the Miyoo's 32-bit one, built for x86-64
    Linux and linked for it."""
    from decompnet.port.wasm import build as native
    repo = tmp_path / "save"
    out = repo / "port" / "wasm" / "build"
    (out / "c").mkdir(parents=True)
    (out / "c" / "game_0.c").write_text("")
    monkeypatch.setattr(native, "build", lambda *a, **k: None)
    monkeypatch.setattr(toolchains, "binary", lambda name: tmp_path / name)
    monkeypatch.setattr(toolchains, "ensure", lambda name, log=print: tmp_path / name)
    compiled, ran = [], []
    monkeypatch.setattr(native, "_parallel", lambda units, work, log, what, step: compiled.append(list(units)) or [])
    monkeypatch.setattr(native, "_run", lambda argv, *a, **k: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    (out / "steamdeck").mkdir()
    (out / "steamdeck" / "game").write_bytes(b"\x7fELF")
    assert steamdeck.build(repo, log=lambda *a: None) == out / "steamdeck" / "game"
    names = {u.name for u in compiled[-1]}
    assert {"live.c", "sdl.c", "wasm-rt-mem-impl.c", "game_0.c", "interp.c"} <= names
    assert "miyoo.c" not in names and "mem32.c" not in names
    link = [str(a) for a in ran[-1]]
    assert link[2:5] == ["-target", "x86_64-linux-gnu.2.28", "-mcpu=znver2"] and "-ldl" in link


def test_the_window_starts_full_screen_when_asked():
    text = (KIT / "window" / "sdl.c").read_text()
    assert 'getenv("PORT_FULLSCREEN")' in text and "SetWindowFullscreen(window, true)" in text


def test_the_steam_deck_host_compiles_for_x86_64_linux(tmp_path):
    """live.c and window/sdl.c built as the export builds them (the pinned Zig, glibc 2.28, the Deck's Zen 2), from
    whichever computer exports."""
    zig, wabt = toolchains.binary("zig"), toolchains.root() / "wabt"
    if not zig.is_file() or not (wabt / "include" / "wasm-rt.h").is_file():
        pytest.skip("needs the pinned Zig and WABT")
    rt = wabt / "share" / "wabt" / "wasm2c"
    env = {**os.environ, "ZIG_GLOBAL_CACHE_DIR": str(tmp_path / "zc"), "ZIG_LOCAL_CACHE_DIR": str(tmp_path / "zc")}
    for unit in (KIT / "live.c", KIT / "window" / "sdl.c", rt / "wasm-rt-mem-impl.c"):
        done = subprocess.run([str(zig), "cc", *steamdeck.TARGET, *steamdeck.FLAGS, "-I", str(wabt / "include"),
                               "-I", str(rt), "-c", str(unit), "-o", str(tmp_path / (unit.stem + ".o"))],
                              capture_output=True, text=True, env=env, timeout=300)
        assert done.returncode == 0, done.stderr
