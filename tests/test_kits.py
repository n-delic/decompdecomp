"""The kits connector (kits/): compilers and SDKs users bring. Every kit says what Setup shows, is a local
toolchain nothing downloads, and gives its folder, build and compilers through the connector; nothing outside kits/
and the consoles that take a kit names one. The XDK kit on a homemade folder (no Microsoft file: empty stand-ins and
the version record a library carries)."""

import json
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


def version_resource(version: str) -> bytes:
    """A stand-in for a program's version resource: just the VS_VERSIONINFO key and its VS_FIXEDFILEINFO."""
    a, b, c, d = (int(p) for p in version.split("."))
    return (b"\0" * 6 + "VS_VERSION_INFO".encode("utf-16-le") + b"\0\0\0\0" +
            struct.pack("<IIIII", 0xFEEF04BD, 0x10000, a << 16 | b, c << 16 | d, 0))


def make_xdk(top: Path, build: int, compiler: str = "7.0.9254.0") -> Path:
    """An XDK-shaped folder: top/xbox with its headers, a library carrying the XDK's version record, a compiler."""
    root = top / "xbox"
    for rel in ("include/XTL.h", "bin/vc7/CL.Exe"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"")
    (root / "bin/vc7/C2.Dll").write_bytes(version_resource(compiler))
    (root / "lib").mkdir(exist_ok=True)
    (root / "lib" / "xapilib.lib").write_bytes(b"!<arch>\n" + b"XAPILIB\0" + struct.pack("<4H", 1, 0, build, 0x4001))
    return root


def make_vc(top: Path, c2: str, layout: str = "VC/bin") -> Path:
    """A Visual C++ folder made by hand: an empty cl.exe, a c2.dll that is only its version resource, an include
    folder. `layout` is where the compiler is inside `top`; the copy returned is the folder above its bin."""
    bin_ = top / layout
    bin_.mkdir(parents=True)
    (bin_ / "cl.exe").write_bytes(b"")
    (bin_ / "c2.dll").write_bytes(version_resource(c2))
    base = bin_
    while base.name.lower() != "bin":
        base = base.parent
    base = base.parent
    (base / "include").mkdir(exist_ok=True)
    return base


@pytest.fixture
def xdk(tmp_path):
    make_xdk(tmp_path / "XDK", 3911)
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
    assert own.version == "xdk-3911" and own.exe.name == "CL.Exe" and own.includes == (folder / "include",)
    assert own.build == "7.0.9254.0" and "7.0.9254.0" in own.name
    args = pc.own_compiler_args([own])
    assert pc.own_versions(["--msvc", "x", *args]) == {"xdk-3911"}
    assert args[1] == f"xdk-3911=wibo,{own.exe},{folder / 'include'}"  # (what tools/configure.py --own reads)


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "toolchains"))


def test_a_kit_has_several_copies_each_checked_listed_and_removable(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    a, b = make_xdk(tmp_path / "a", 3911), make_xdk(tmp_path / "b", 4361, "7.0.9500.0")

    def install(folder):
        return toolchains.install("xdk", folder=folder, accept_license=True, log=lambda *_: None)

    install(a)
    install(b)
    install(a)  # (the same copy again: still one)
    assert kits.folders("xdk") == [a.resolve(), b.resolve()] and kits.folder("xdk") == a.resolve()
    with pytest.raises(toolchains.ToolchainError, match="doesn't look like"):
        install(tmp_path / "empty")
    assert len(kits.folders("xdk")) == 2  # (a refused copy changes nothing)
    row = next(r for r in toolchains.status() if r["id"] == "xdk")
    assert [c["build"] for c in row["copies"]] == ["3911", "4361"] and row["folder"] == str(a.resolve())
    assert {c.version for c in kits.compilers(["xdk"])} == {"xdk-3911", "xdk-4361"}  # (one compiler per copy)
    toolchains.remove_local("xdk", a.resolve())
    assert kits.folders("xdk") == [b.resolve()]
    with pytest.raises(toolchains.ToolchainError, match="isn't one of your copies"):
        toolchains.remove_local("xdk", a.resolve())
    toolchains.remove_local("xdk", b.resolve())
    assert kits.folders("xdk") == [] and toolchains.installed("xdk") is None


def test_a_manifest_from_before_several_copies_still_reads(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    a = make_xdk(tmp_path / "a", 3911)
    toolchains.install("xdk", folder=a, accept_license=True, log=lambda *_: None)
    manifest = toolchains.path("xdk", False) / toolchains.MANIFEST
    old = json.loads(manifest.read_text(encoding="utf-8"))
    del old["folders"]
    manifest.write_text(json.dumps(old), encoding="utf-8")
    assert kits.folders("xdk") == [a.resolve()]
    toolchains.install("xdk", folder=make_xdk(tmp_path / "b", 4361), accept_license=True, log=lambda *_: None)
    assert len(kits.folders("xdk")) == 2  # (the old copy kept, the new one added)


def test_the_version_comes_from_the_version_resource(tmp_path):
    f = tmp_path / "c2.dll"
    f.write_bytes(b"MZ" + bytes(100) + version_resource("13.10.3077.0") + bytes(50))
    assert kits.file_version(f) == "13.10.3077.0"
    f.write_bytes(b"MZ" + bytes(100))
    assert kits.file_version(f) == "" and kits.file_version(tmp_path / "none.dll") == ""


@pytest.mark.parametrize("layout,c2,runner", [
    ("VC98/Bin", "12.0.8168.0", "wibo"), ("VC/bin", "16.0.40219.1", "wibo"),
    ("VC/Tools/MSVC/14.16.27023/bin/Hostx86/x86", "19.16.27023.0", "wine")])
def test_visual_cpp_is_recognised_in_its_layouts_and_run_by_the_right_runner(tmp_path, layout, c2, runner):
    top = tmp_path / "vs"
    copy = make_vc(top, c2, layout)
    kit = kits.get("visualcpp")
    for given in (copy, top):  # (the compiler's folder, or the Visual Studio folder above it)
        assert kit.locate(given) == copy.resolve()
    [own] = kit.compilers(copy.resolve())
    assert own.runner == runner and own.build == c2 and kit.build(copy) == c2
    assert own.version == "own-" + ".".join(c2.split(".")[:3]) and own.includes[0] == copy.resolve() / "include"
    assert kit.locate(tmp_path / "nothing") is None


def test_a_2017_copy_finds_the_windows_sdk_beside_it_and_says_when_it_cant(tmp_path):
    kit = kits.get("visualcpp")
    copy = make_vc(tmp_path / "vs" / "VC" / "Tools" / "MSVC" / "14.16.27023", "19.16.27023.0", "bin/Hostx86/x86")
    assert "no Windows SDK" in kit.verify(copy)
    inc = tmp_path / "vs" / "VC" / "Windows Kits" / "10" / "Include" / "10.0.17763.0"
    for sub in ("ucrt", "um", "shared"):
        (inc / sub).mkdir(parents=True)
    [own] = kit.compilers(copy)
    assert own.includes[1:] == (inc / "ucrt", inc / "um", inc / "shared")
    assert kit.verify(copy) == "Visual C++ 19.16.27023.0"


def test_setup_lists_every_kit_a_console_takes():
    """The Compilers & tools page shows what /api/state sends: the compilers, the tools and the consoles' packs and
    kits (app/engine TOOLS). A kit missing from it can't be set up at all."""
    from decompnet.app.engine import TOOLS

    assert {kid for c in consoles.CONSOLES for kid in c.kits} <= set(TOOLS)
