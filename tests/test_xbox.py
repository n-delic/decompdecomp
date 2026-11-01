"""The original Xbox (consoles/xbox/): an XBE read, made a Windows program at the same addresses (pe_view) that the PC
path takes as it is, the same game from its disc image, kernel calls named and matched, and the XDK's library
sections set aside. On the demo game (projects/demo-xbox), so it skips without Visual C++ (the `msvc` pack); the
import also needs Ghidra. tests/test_consoles.py runs the demo through every console's path besides."""

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import consoles, toolchains
from decompnet.app.engine import RomNetwork
from decompnet.consoles.pc import scratch as pc
from decompnet.consoles.xbox import XBOX, xdvdfs
from decompnet.consoles.xbox import scratch as xbox
from decompnet.forks.project import ForkError
from tests.test_kits import make_xdk

DEMO = Path(__file__).resolve().parent.parent / "projects" / "demo-xbox"
KERNEL_CALL = """extern "C" __declspec(dllimport) long __stdcall NtClose(void *handle);

s32 {name}(void *handle) {{
    return NtClose(handle);
}}
"""


def _installed(*tools) -> bool:
    return all(toolchains.installed(t) is not None for t in tools)


@pytest.fixture(scope="module")
def demo():
    if not _installed("msvc", "wibo"):
        pytest.skip("the Xbox demo is built with Visual C++ 7.1 (the msvc pack) through wibo")
    key = DEMO / "build" / "demo.json"
    if not key.exists():
        subprocess.run([sys.executable, str(DEMO / "build.py")], check=True)
    built = json.loads(key.read_text())
    return DEMO / "build" / built["game"], built["symbols"]


def _xiso(xbe: bytes) -> bytes:
    """An XISO holding only default.xbe (the filesystem from byte 0: its descriptor at sector 32, the root at 33)."""
    S = xdvdfs.SECTOR
    name = b"default.xbe"
    root = struct.pack("<HHIIBB", 0, 0, 34, len(xbe), 0x80, len(name)) + name
    img = bytearray(34 * S + len(xbe))
    img[32 * S:32 * S + 20] = xdvdfs.MAGIC
    struct.pack_into("<II", img, 32 * S + 0x14, 33, len(root))
    img[33 * S:33 * S + len(root)] = root
    img[34 * S:] = xbe
    return bytes(img)


def test_an_xbe_reads_and_its_pe_view_is_the_same_program(demo):
    game, symbols = demo
    data = game.read_bytes()
    xbe = xbox.read_xbe(data)
    assert (xbe.title, xbe.title_code, xbe.keys, xbox.xdk_build(xbe)) == ("Demo Quest", "DQ-001", "retail", 5849)
    assert xbe.entry == symbols["_start"] and xbe.kernel_thunk == symbols["_xboxkrnl_thunks"]
    pe = pc.read_pe(xbox.pe_view(data))
    assert pe.entry == xbe.entry and pe.image_base < xbe.base  # (the PE's own headers below: nothing moves)
    for s in xbe.sections:  # every section's bytes at its own address, code where the XBE has code
        assert pe.read(s.va, s.size) == data[s.offset:s.offset + s.size]
        assert pe.section_at(s.va).code == s.code
    assert pe.imports == {symbols["_xboxkrnl_thunks"]: "NtClose"}
    assert xbox.game_info(game)["compiler"] == "7.1"


def test_a_disc_image_is_the_same_game(demo, tmp_path):
    game, _ = demo
    disc = tmp_path / "game.iso"
    disc.write_bytes(_xiso(game.read_bytes()))
    assert consoles.identify(disc) is XBOX and consoles.identify(game) is XBOX
    assert XBOX.identity(disc) == XBOX.identity(game)
    assert XBOX.upload_name(game.read_bytes()) == "default.xbe" and XBOX.upload_name(disc.read_bytes()) == "game.iso"
    # a saved project's program is made again from either, and only from the same game
    sha1 = XBOX.identity(game)
    dest = tmp_path / "orig" / "demoquest.exe"
    assert XBOX.prepare_fork_game(disc, dest, sha1) and hashlib.sha1(dest.read_bytes()).hexdigest() == sha1
    with pytest.raises(ForkError, match="isn't this project's game"):
        XBOX.prepare_fork_game(game, dest, "0" * 40)


def test_a_damaged_xbe_says_which_field(demo):
    data = bytearray(demo[0].read_bytes())
    struct.pack_into("<I", data, 0x120, 0x7FFF0000)  # the section headers' address, far outside the file
    with pytest.raises(ForkError, match="section headers"):
        xbox.read_xbe(bytes(data))


def test_the_xdks_library_sections_are_set_aside(demo, tmp_path):
    xbe = xbox.read_xbe(demo[0].read_bytes())
    assert xbox.library_sections(xbe) == set()
    xbe.sections[0].name = "DSOUND"  # (the demo's .text, as if the XDK's DirectSound had linked it)
    assert xbox.library_sections(xbe) == {"DSOUND"}
    xbe.sections[2].name = "XAPILIB"  # named after a library of the version table, but data: not code
    assert xbox.library_sections(xbe) == {"DSOUND"}
    xbe.sections[0].name = "BINK32A"  # RAD's Bink video, licensed middleware, in sections of its own
    assert xbox.library_sections(xbe) == {"BINK32A"}


@pytest.mark.parametrize("build, compiler", [(5849, "7.1"), (5933, "7.1"), (4627, "7.0"), (3911, "7.0")])
def test_the_xdk_build_names_the_compiler(demo, tmp_path, build, compiler):
    data = bytearray(demo[0].read_bytes())
    libs = xbox._u32(data, 0x164) - xbox.BASE
    for i in range(xbox._u32(data, 0x160)):  # every library of the version table from that XDK
        struct.pack_into("<H", data, libs + 16 * i + 12, build)
    game = tmp_path / "default.xbe"
    game.write_bytes(data)
    info = xbox.game_info(game)
    assert info["compiler"] == compiler and str(build) in info["compiler_why"]


@pytest.fixture(scope="module")
def project(demo, tmp_path_factory):
    if not _installed("objdiff-cli", "ghidra"):
        pytest.skip("an Xbox import needs objdiff and Ghidra")
    work = tmp_path_factory.mktemp("xbox")
    net = RomNetwork(work / "app.db", work)
    return net, net.import_rom(demo[0], confirmed=True, log=lambda *_: None)


def test_a_kernel_call_is_an_import_and_c_calling_it_matches(demo, project):
    _, symbols = demo
    net, slug = project
    pid = net.project_row(slug)["id"]
    address = f"{symbols['_close_handle']:08X}"  # (a project's functions go by their address)
    name = next(r["name"] for r in net.store.all("SELECT name FROM functions WHERE project_id=?", pid)
                if address in r["name"].upper())
    fp = net.forks.get(slug)[1]
    info = json.loads((fp.root / "config" / "demoquest" / "functions.json").read_text())
    entry = next(fn for fns in info["units"].values() for fn in fns if fn["name"] == name)
    assert [r[2] for r in entry["relocs"]] == ["__imp__NtClose"]
    agent, _ = net.local_agent("xbox-agent")
    net.claim(agent, slug, name)
    res = net.attempt(agent, name, KERNEL_CALL.format(name=name.lstrip("_")), slug)
    assert res["matched"], res["text"]


def test_the_rebuild_gives_back_the_xbe(demo, project):
    net, slug = project
    assert net.forks.check_rom(slug, log=lambda *_: None)["identical"]
    rebuilt = net.forks.get(slug)[1].root / "build" / "demoquest" / "default.xbe"
    assert rebuilt.read_bytes() == demo[0].read_bytes()  # (your copy's XBE, byte for byte, its code from the C)


def test_an_early_game_builds_with_its_own_xdks_compiler(demo, tmp_path, monkeypatch):
    """Before XDK 5233 each XDK shipped a Visual C++ 7.0 build of its own (13.00.9254 in 3911, not the pack's 9466):
    with the user's XDK of that build set up, the game builds with it."""
    data = bytearray(demo[0].read_bytes())
    libs = xbox._u32(data, 0x164) - xbox.BASE
    for i in range(xbox._u32(data, 0x160)):
        struct.pack_into("<H", data, libs + 16 * i + 12, 3911)
    game = tmp_path / "default.xbe"
    game.write_bytes(data)
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "toolchains"))
    for build in (4361, 3911):  # (two XDKs set up: the game's is the one picked, whichever was added first)
        toolchains.install("xdk", folder=make_xdk(tmp_path / f"xdk{build}", build), accept_license=True,
                           log=lambda *_: None)
    info = xbox.game_info(game)
    assert info["compiler"] == "xdk-3911" and "XDK 3911" in info["compiler_why"] and info["xdk_build"] == 3911
    assert {c.id for c in consoles.get("xbox").compilers} >= {"MSVC/xdk-3911", "MSVC/xdk-4361"}
    toolchains.remove_local("xdk", (tmp_path / "xdk3911" / "xbox").resolve())
    info = xbox.game_info(game)  # no copy of the game's XDK: the nearest the pack has, and what to set up
    assert info["compiler"] == "7.0" and "XDK 3911 in Setup" in info["compiler_why"]


def test_only_visual_cpp_71_builds_with_arch_sse():
    assert "/arch:SSE" in xbox.cl_flags("7.1")
    assert "/arch:SSE" not in xbox.cl_flags("7.0") and "/arch:SSE" not in xbox.cl_flags("xdk-3911")
