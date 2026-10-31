"""The kits connector (kits/): compilers and SDKs users bring. Every kit says what Setup shows, is a local
toolchain nothing downloads, and gives its folder, build and compilers through the connector; nothing outside kits/
and the consoles that take a kit names one. The XDK kit on a homemade folder (no Microsoft file: empty stand-ins and
the version record a library carries)."""

import re
import struct
from pathlib import Path

import pytest

from decompnet import consoles, kits, toolchains
from decompnet.consoles.pc import scratch as pc

SRC = Path(__file__).resolve().parent.parent / "src" / "decompnet"


@pytest.mark.parametrize("kit", kits.KITS, ids=[k.id for k in kits.KITS])
def test_every_kit_says_what_setup_shows_and_is_a_local_toolchain(kit):
    assert kit.id and kit.name and kit.platform and kit.description and kit.files
    tc = toolchains.get(kit.id)
    assert tc.local and tc.supported and tc.local_files == list(kit.files) and not tc.parts  # (nothing to download)
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in kit.known)
    assert any(kit.id in c.kits for c in consoles.CONSOLES), "a kit no console takes"


def test_only_kits_and_their_consoles_name_a_kit():
    takers = {f"consoles/{c.id}/" for c in consoles.CONSOLES if c.kits} | {"consoles/xbox/"}
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("kits/") or any(rel.startswith(t) for t in takers):
            continue
        text = path.read_text(encoding="utf-8")
        for kit in kits.KITS:
            assert f'"{kit.id}"' not in text and f"'{kit.id}'" not in text, f"{rel} names the kit {kit.id}"


@pytest.fixture
def xdk(tmp_path):
    """An XDK-shaped folder: XDK/xbox with its headers, a library carrying XDK 3911's version record, a compiler."""
    root = tmp_path / "XDK" / "xbox"
    for rel in ("include/XTL.h", "bin/vc7/CL.Exe", "bin/vc7/C2.Dll"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"")
    (root / "lib").mkdir()
    (root / "lib" / "xapilib.lib").write_bytes(b"!<arch>\n" + b"XAPILIB\0" + struct.pack("<4H", 1, 0, 3911, 0x4001))
    return tmp_path


def test_a_kit_is_set_up_from_the_users_folder_and_never_copied(xdk, tmp_path, monkeypatch):
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "toolchains"))
    with pytest.raises(toolchains.ToolchainError, match="proprietary"):
        toolchains.install("xdk", folder=xdk, log=lambda *_: None)
    assert kits.folder("xdk") is None
    found = toolchains.install("xdk", folder=xdk, accept_license=True, log=lambda *_: None)
    assert found == (xdk / "XDK" / "xbox").resolve() == kits.folder("xdk")  # (found inside the folder picked)
    assert not any(p.suffix.lower() in (".h", ".lib", ".exe", ".dll") for p in (tmp_path / "toolchains").rglob("*"))
    row = next(r for r in toolchains.status() if r["id"] == "xdk")
    assert row["installed"] and "doesn't know" in row["verified"]  # (an empty c2.dll is no build it knows)


def test_the_xdk_kit_gives_its_build_and_compiler_to_the_build(xdk):
    kit, folder = kits.get("xdk"), xdk / "XDK" / "xbox"
    assert kit.build(folder) == "3911"
    [own] = kit.compilers(folder)
    assert own.version == "xdk" and own.exe.name == "CL.Exe" and own.includes == (folder / "include",)
    args = pc.own_compiler_args([own])
    assert pc.own_versions(["--msvc", "x", *args]) == {"xdk"}
    assert args[1] == f"xdk=wibo,{own.exe},{folder / 'include'}"  # (what tools/configure.py --own reads)
