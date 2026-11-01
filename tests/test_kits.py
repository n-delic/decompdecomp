"""The kits connector (kits/): compilers and SDKs users bring. Every kit says what Setup shows, is a local
toolchain nothing downloads, and gives its folder, build and compilers through the connector; nothing outside kits/
and the consoles that take a kit names one. The XDK kit on a homemade folder (no Microsoft file: empty stand-ins and
the version record a library carries)."""

import json
import re
import struct
import sys
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


def test_visualcpp_reads_old_32_bit_copies_from_their_folders_and_refuses_16_bit_ones(tmp_path):
    """#137: Visual C++ 2.x (MSVC20), 4.x (MSDEV) and 5.0 (DevStudio/VC) keep cl.exe and c2 in a bin folder; the
    version is c2.dll's, else c2.exe's. 1.x (cl 8.x) is 16-bit."""
    kit = kits.get("visualcpp")
    two = make_vc(tmp_path / "a", "9.0.2196.0", "MSVC20/BIN")
    four = make_vc(tmp_path / "b", "10.20.6166.0", "MSDEV/BIN")
    five = make_vc(tmp_path / "c", "11.0.7022.0", "DevStudio/VC/BIN")
    for copy, build in ((two, "9.0.2196.0"), (four, "10.20.6166.0"), (five, "11.0.7022.0")):
        assert kit.locate(copy.parent) == copy.resolve()  # (the product's folder: MSVC20, MSDEV, DevStudio)
        [own] = kit.compilers(copy)
        assert own.build == build and own.runner == "wibo" and own.includes == (copy / "include",)
    exe = make_vc(tmp_path / "d", "9.0.2196.0", "MSVC20/BIN")
    (exe / "BIN" / "c2.dll").rename(exe / "BIN" / "c2.exe")
    assert kit.build(exe) == "9.0.2196.0"
    sixteen = make_vc(tmp_path / "e", "8.0.2000.0", "MSVC/BIN")
    assert kit.compilers(sixteen) == [] and "16-bit" in kit.verify(sixteen)


# ---- the PS3 SDK kit, and a kit's compiler in ROM mode (stand-ins: a shell script is the "compiler")

STAMP = b"4.1.1 (SDK270, $Rev: 3019 $)"
FAKE_GCC = (b"#!/bin/sh\n# " + STAMP + b"\nwhile [ $# -gt 0 ]; do [ \"$1\" = -o ] && out=$2; args=\"$args $1\"; shift; done\n"
            b"echo \"ran from $0:$args\" > \"$out\"\n")


def make_ps3sdk(top: Path, *, host: str = "linux", stamp: bytes = STAMP) -> Path:
    """An SDK-shaped folder: top/cell with a stand-in PPU GCC for the host (a script that records how it was run),
    the SDK's header folders and Sony's intrinsics header a free pack lacks."""
    root = top / "cell"
    exe = root / ("host-linux/ppu/bin/ppu-lv2-gcc" if host == "linux" else "host-win32/ppu/bin/ppu-lv2-gcc.exe")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(FAKE_GCC.replace(STAMP, stamp))
    exe.chmod(0o755)
    (root / "target/ppu/include/sys").mkdir(parents=True)
    (root / "target/ppu/include/sys/process.h").write_text("#define FROM_SDK 1\n")
    (root / "target/common/include").mkdir(parents=True)
    (root / "target/common/include/vmx2spu.h").write_text("")
    return root


@pytest.mark.parametrize("here", ["linux", "darwin", "win32"])
def test_the_ps3_sdk_is_recognised_by_its_compiler_in_any_layout(tmp_path, monkeypatch, here):
    from decompnet.kits import ps3sdk

    kit = kits.get("ps3sdk")
    linux, win = make_ps3sdk(tmp_path / "l"), make_ps3sdk(tmp_path / "w", host="win")
    monkeypatch.setattr(ps3sdk.sys, "platform", here)  # (CI on macOS: a Linux host's copy said no build)
    for top, copy in ((tmp_path / "l", linux), (tmp_path / "w", win)):
        assert kit.locate(top) == kit.locate(copy) == copy.resolve()  # (the folder above, or the SDK itself)
        assert kit.build(copy) == "SDK270" and kit.verify(copy) == "PlayStation 3 SDK SDK270"
    assert kit.locate(tmp_path / "nothing") is None
    [own] = kit.compilers(win)  # (a Windows host: wibo runs it)
    assert own.version == "ps3sdk-270" and own.runner == "wibo" and own.build == "SDK270" and own.root == win
    assert own.includes == (win / "target/ppu/include", win / "target/common/include") and own.args == ("-c",)


@pytest.mark.skipif(sys.platform != "linux", reason="the stand-in compiler is a script of the Linux host")
def test_a_rom_project_compiles_through_a_kit_compiler_from_the_users_copy(tmp_path, monkeypatch):
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config

    _setup(monkeypatch, tmp_path)
    sdk = make_ps3sdk(tmp_path / "sdk")
    ps3 = consoles.get("ps3")
    assert "ps3sdk-270" not in [c.id for c in ps3.compilers] and not toolchains.known("ps3sdk-270")
    toolchains.install("ps3sdk", folder=sdk, accept_license=True, log=lambda *_: None)
    # the console lists it with its own, and the registry knows it without installing anything
    assert [c.id for c in ps3.compilers][-1] == "ps3sdk-270" and toolchains.known("ps3sdk-270")
    assert toolchains.installed("ps3sdk-270") and toolchains.get("ps3sdk-270").flags == ["-O2"]
    assert ps3.default_flags("ps3sdk-270", 0) == ["-O2", "-falign-functions=4"]
    cfg = compiler_config(tmp_path, {"compiler": {"toolchain": "ps3sdk-270", "flags": ["-O1"]}})
    assert str(sdk.resolve()) in cfg.ro_paths  # (the copy is read in the sandbox, never copied)
    result = compile_source(cfg, "int x;", "int f(void) { return 1; }")
    assert result.ok, result.log
    ran = result.obj.decode()
    assert f"ran from {sdk.resolve()}/host-linux/ppu/bin/ppu-lv2-gcc:" in ran
    assert " -c -O1 " in ran and f"-I{sdk.resolve()}/target/ppu/include" in ran
    assert not any(p.suffix in (".h", ".o") for p in (tmp_path / "toolchains").rglob("*"))
    toolchains.remove_local("ps3sdk", sdk.resolve())  # (the copy is forgotten: so is its compiler)
    assert not toolchains.known("ps3sdk-270") and toolchains.installed("ps3sdk-270") is None
    with pytest.raises(toolchains.ToolchainError, match="isn't one decompdecomp has"):
        compiler_config(tmp_path, {"compiler": {"toolchain": "ps3sdk-270"}})


def test_a_ps3_game_takes_the_sdk_copy_whose_stamp_is_its_own_else_the_free_compiler_and_says_why(tmp_path, monkeypatch):
    from decompnet.consoles.ps3 import elf
    from tests.test_ps3 import program

    _setup(monkeypatch, tmp_path)
    ps3 = consoles.get("ps3")
    for top, stamp in (("a", STAMP), ("b", b"4.1.1 (SDK360, $Rev: 3455 $)")):
        toolchains.install("ps3sdk", folder=make_ps3sdk(tmp_path / top, host="win", stamp=stamp), accept_license=True,
                           log=lambda *_: None)
    assert {c.id for c in ps3.compilers} >= {"ps3sdk-270", "ps3sdk-360"}  # (two copies, both listed)

    def info_of(stamp: bytes) -> dict:
        return elf.read_elf(program(sections=True, comment=b"GCC: (GNU) " + stamp + b"\0"))[1]

    info = info_of(b"4.1.1 (SDK360, $Rev: 3455 $)")
    assert info["sdk"] == "SDK360" and info["compiler_guess"] == "PS3-GCC-4"
    assert ps3.default_compiler(info, "") == "ps3sdk-360" and "your SDK copy" in info["compiler_why"]
    assert info_of(b"4.1.1 (CELL 4.1.2.7, RTTI, $Rev: 1830 $)")["sdk"] == "CELL 4.1.2.7"
    info = info_of(b"4.1.1 (SDK420, $Rev: 3547 $)")  # (a generation the user has no copy of)
    assert ps3.default_compiler(info, "") == "ps3dev-gcc"
    assert "SDK420" in info["compiler_why"] and "Setup" in info["compiler_why"]
    assert ps3.default_compiler({"compiler_guess": "PS3DEV"}, "") == "ps3dev-gcc"  # (no stamp: as before)
