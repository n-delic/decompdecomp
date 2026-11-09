import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest

from decompnet import limits, toolchains

# Archives _extract opens with a third-party module rather than the standard library.
_EXTERNAL_EXTRACTORS = {".zst": "zstandard"}


def _marker_environment(platform_name: str) -> dict[str, str]:
    system = platform_name.split("-", 1)[0]
    return {
        "linux": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix"},
        "macos": {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix"},
        "windows": {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"},
        "wsl": {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"},
    }[system]


def _marker_allows(marker: str, platform_name: str) -> bool:
    """Whether a marker in pyproject.toml applies on `platform_name` (our markers are simple sys_platform ones)."""
    return not marker.strip() or bool(eval(marker, {"__builtins__": {}}, _marker_environment(platform_name)))


@pytest.fixture
def tc_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path / "toolchains"))
    return tmp_path


def test_registry_is_pinned_and_labelled():
    reg = toolchains.registry()
    assert {"gcc-2.7.2-kmc", "ee-gcc-2.96", "ido-7.1", "m2c", "runtime-i386"} <= set(reg)
    for tc in reg.values():
        assert tc.license and tc.source, tc.id
        if not tc.proprietary:  # everything we install by default is verified against a pinned hash
            assert all(p["sha256"] for p in tc.parts) or tc.commit, tc.id
    assert reg["ido-7.1"].proprietary and not reg["gcc-2.7.2-kmc"].proprietary
    assert reg["ee-gcc-2.96"].requires == ["runtime-i386"]


@pytest.mark.parametrize("tid", ["ido-7.1", "ido-5.3"])
def test_windows_ido_brings_the_msys_runtime_its_executables_import(tid):
    """#130: ido-static-recomp's Windows builds are MSYS2 programs: cc.exe and its siblings import msys-2.0.dll,
    which the release archive doesn't ship, so every compile failed with "msys-2.0.dll was not found". The pinned
    MSYS2 runtime package puts the DLL beside cc.exe."""
    parts = toolchains.get(tid).parts_for("windows-x86_64")
    runtime = [p for p in parts if p.get("keep", {}).get("usr/bin/msys-2.0.dll") == "msys-2.0.dll"]
    assert len(runtime) == 1
    part = runtime[0]
    assert part["platforms"] == ["windows-x86_64"] and "into" not in part
    assert part["url"].startswith("https://repo.msys2.org/msys/x86_64/msys2-runtime-")
    assert part["url"].endswith("-x86_64.pkg.tar.zst") and len(part["sha256"]) == 64
    assert part["mkdir"] == ["tmp", "var/tmp"]  # its /tmp and /var/tmp, under the root the DLL makes


def test_every_platform_that_unpacks_an_external_archive_form_has_its_extractor():
    """#130: Windows IDO's MSYS2 runtime is a .pkg.tar.zst, and _extract imports zstandard for .zst. zstandard was a
    Linux-only dependency, so the install broke on Windows, the only platform that downloads that part. Every
    platform a part's archive form is for must have the module its extractor imports."""
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    markers = {}
    for requirement in pyproject["project"]["dependencies"]:
        spec, _, marker = requirement.partition(";")
        markers[spec.strip().split(">")[0].split("[")[0]] = marker
    for suffix, dependency in _EXTERNAL_EXTRACTORS.items():
        platforms = {platform for tc in toolchains.registry().values() for part in tc.parts
                     if part["url"].endswith(suffix)
                     for platform in part.get("platforms", [toolchains.current_platform()])}
        for platform in platforms:
            assert _marker_allows(markers[dependency], platform), \
                f"{dependency} is needed on {platform} to unpack a {suffix} part"


def test_an_unknown_toolchain_id_is_known_to_be_missing():
    """#118: a project from another tool names a compiler decompdecomp doesn't have. Opening or listing the project
    asks the registry whether it has the id (known), instead of get() raising at it."""
    assert toolchains.known("gcc-2.7.2-kmc") and toolchains.known("m2c")
    assert not toolchains.known("msvcxx-x86_64")
    with pytest.raises(toolchains.ToolchainError, match="unknown toolchain"):
        toolchains.get("msvcxx-x86_64")


def test_proprietary_toolchains_need_explicit_acceptance(tc_root):
    with pytest.raises(toolchains.ToolchainError, match="--accept-license"):
        toolchains.install("ido-7.1", log=lambda *_: None)
    assert toolchains.installed("ido-7.1") is None


def test_an_already_installed_proprietary_tool_needs_no_new_acceptance(tc_root):
    directory = toolchains.path("ido-7.1")
    directory.mkdir(parents=True)
    (directory / toolchains.MANIFEST).write_text('{"license_accepted":true}')
    assert toolchains.install("ido-7.1", log=lambda _: None) == directory
    with pytest.raises(toolchains.ToolchainError, match="accept-license"):
        toolchains.install("ido-7.1", force=True, log=lambda _: None)


def test_an_arm_computer_is_told_why_nothing_compiles(tc_root, monkeypatch):
    """Issue #18: on an ARM Linux computer the games' compilers (x86 programs) don't install: the error says why."""
    monkeypatch.setattr(toolchains._platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(toolchains.sys, "platform", "linux")
    assert toolchains.current_platform() == "linux-aarch64" and not toolchains.get("ee-gcc-2.96").supported
    with pytest.raises(toolchains.ToolchainError, match=r"linux-aarch64\): decompdecomp needs a 64-bit Intel or AMD PC"):
        toolchains.install("ee-gcc-2.96", log=lambda *_: None)
    monkeypatch.setattr(toolchains._platform, "machine", lambda: "x86_64")
    assert toolchains.unsupported_cpu() is None


def _fake(monkeypatch, tc_root, sha):
    src = tc_root / "src"
    src.mkdir()
    (src / "cc").write_text("#!/bin/sh\n")
    archive = tc_root / "fake.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        t.add(src / "cc", arcname="cc")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    fake = toolchains.Toolchain(id="fake", name="Fake", license="MIT", source="test",
                                parts=[{"url": archive.as_uri(), "sha256": sha or digest}])
    monkeypatch.setattr(toolchains, "registry", lambda: {"fake": fake})
    original_registered = toolchains._registered
    monkeypatch.setattr(toolchains, '_registered', lambda tid: toolchains.registry().get(tid) or original_registered(tid))
    return digest


def test_install_verifies_checksums(tc_root, monkeypatch):
    _fake(monkeypatch, tc_root, sha="0" * 64)
    with pytest.raises(toolchains.ToolchainError, match="checksum mismatch"):
        toolchains.install("fake", log=lambda *_: None)
    assert not toolchains.path("fake").exists()


def test_install_records_a_manifest(tc_root, monkeypatch):
    digest = _fake(monkeypatch, tc_root, sha=None)
    toolchains.install("fake", log=lambda *_: None)
    m = json.loads((toolchains.path("fake") / toolchains.MANIFEST).read_text())
    assert m["parts"][0]["sha256"] == digest and (toolchains.path("fake") / "cc").exists()
    assert toolchains.require("fake") == toolchains.path("fake")


def test_a_part_creates_the_folders_its_programs_temp_files_need(tc_root, monkeypatch):
    """#130: an MSYS2 program's /tmp and /var/tmp are folders under its own root (beside msys-2.0.dll). Windows
    IDO's cc.exe writes its temporary files there, so the runtime part creates them at install time."""
    _fake(monkeypatch, tc_root, sha=None)
    toolchains.registry()["fake"].parts[0]["mkdir"] = ["tmp", "var/tmp"]
    toolchains.install("fake", log=lambda *_: None)
    assert (toolchains.path("fake") / "tmp").is_dir()
    assert (toolchains.path("fake") / "var" / "tmp").is_dir()


def test_a_folder_of_scripts_gets_an_environment_for_what_it_imports(tc_root, monkeypatch):
    """decomp-permuter is a folder of scripts, not a package: it's unpacked from its pinned archive, and its own
    environment gets only the libraries its scripts import."""
    _fake(monkeypatch, tc_root, sha=None)
    toolchains.registry()["fake"].pip_extra = ["toml==0.10.2"]
    runs = []
    monkeypatch.setattr(toolchains.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(toolchains.subprocess, "run", lambda argv, **kw: runs.append(argv))
    toolchains.install("fake", log=lambda *_: None)
    assert [argv[1] for argv in runs] == ["venv", "pip"]
    assert runs[1][runs[1].index("--python") + 2:] == ["toml==0.10.2"]  # no package of its own to install
    assert (toolchains.path("fake") / "cc").exists()


def test_zst_archives_need_no_zstd_command(tmp_path, monkeypatch):
    """The 32-bit runtime EE-GCC needs is a .tar.zst: GNU tar would run the zstd command for it, which many systems
    don't have (the install then failed with "command failed (2)")."""
    zstandard = pytest.importorskip("zstandard")
    if not shutil.which("tar"):
        pytest.skip("needs tar")
    (tmp_path / "src" / "usr" / "lib32").mkdir(parents=True)
    (tmp_path / "src" / "usr" / "lib32" / "ld-linux.so.2").write_bytes(b"\x7fELF")
    plain = tmp_path / "pkg.tar"
    with tarfile.open(plain, "w") as t:
        t.add(tmp_path / "src" / "usr", arcname="usr")
    archive = tmp_path / "pkg.tar.zst"
    archive.write_bytes(zstandard.ZstdCompressor().compress(plain.read_bytes()))
    if os.name != "nt":  # a zstd command that always fails, as a missing one does: tar must not need it
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "zstd").write_text("#!/bin/sh\nexit 2\n")
        (tmp_path / "bin" / "zstd").chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    dest = tmp_path / "out"
    dest.mkdir()
    toolchains._extract(archive, dest, "usr/lib32")
    assert (dest / "ld-linux.so.2").read_bytes() == b"\x7fELF"
    assert not list(tmp_path.glob("tmp*"))  # no leftovers


def test_keep_takes_only_what_a_build_needs_and_merges_folders(tmp_path):
    """Visual C++ 2010's archive is a whole install (700 MB unpacked): its compiler and headers go where the project
    looks for them, the Windows SDK's headers with Visual C++'s, and nothing else is unpacked."""
    import zipfile

    src = tmp_path / "msvc1000-abc"
    for name in ("VC/bin/cl.exe", "VC/include/stdio.h", "Microsoft SDKs/Windows/v7.0A/Include/windows.h",
                 "VC/crt/src/printf.c", "VC/lib/libcmt.lib"):
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(name)
    archive = tmp_path / "msvc.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        t.add(src, arcname=src.name)
    dest = tmp_path / "out"
    dest.mkdir()
    toolchains._extract(archive, dest, "msvc1000-abc", {"VC/bin": "Bin", "Microsoft SDKs/Windows/v7.0A/Include":
                                                        "Include", "VC/include": "Include"})
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()) == [
        "Bin/cl.exe", "Include/stdio.h", "Include/windows.h"]
    # one file of a zip, into a folder that's already there (2010's Bin gets the redistributable's msvcp100.dll)
    with zipfile.ZipFile(tmp_path / "bundle.zip", "w") as z:
        z.writestr("X360/16.00/msvcp100.dll", "dll")
        z.writestr("X360/16.00/cl.exe", "another compiler")
    toolchains._extract(tmp_path / "bundle.zip", dest, None, {"X360/16.00/msvcp100.dll": "Bin/msvcp100.dll"})
    assert sorted(p.name for p in (dest / "Bin").iterdir()) == ["cl.exe", "msvcp100.dll"]
    assert (dest / "Bin" / "cl.exe").read_text() == "VC/bin/cl.exe"
    with pytest.raises(toolchains.ToolchainError, match="has no VC/missing"):
        toolchains._extract(archive, dest, "msvc1000-abc", {"VC/missing": "Bin"})
    assert not list(tmp_path.glob("tmp*"))  # no leftovers


def _java(folder, version, javac):
    folder.mkdir(parents=True, exist_ok=True)
    java = folder / "java"
    java.write_text(f"#!/bin/sh\necho 'openjdk version \"{version}\" 2026-08-18' >&2\n")
    java.chmod(0o755)
    if javac:
        (folder / "javac").write_text("#!/bin/sh\n")
    return folder


@pytest.mark.skipif(os.name == "nt", reason="shell-script stand-ins for java")
def test_a_java_runtime_alone_is_no_jdk(tmp_path, monkeypatch):
    """Ghidra's launcher wants a JDK: with only a Java 25 runtime it quit ("Unable to prompt user for JDK path"),
    and an import said Ghidra found no functions, as if the game were packed."""
    monkeypatch.setenv("PATH", str(_java(tmp_path / "jre", "25.0.4", javac=False)))
    assert toolchains.java_version() == 25 and toolchains.jdk_version() == 0
    monkeypatch.setenv("PATH", str(_java(tmp_path / "jdk", "21.0.12", javac=True)))
    assert toolchains.jdk_version() == 21


def test_ghidra_without_a_jdk_runs_with_the_pinned_one(tc_root, monkeypatch):
    from decompnet.forks import ghidra

    monkeypatch.setattr(toolchains, "jdk_version", lambda: 0)
    assert ghidra.java_home() == toolchains.path("jdk") and not ghidra.available()
    env = ghidra._env()
    assert env["JAVA_HOME"] == str(toolchains.path("jdk")) and env["PATH"].startswith(str(toolchains.path("jdk") / "bin"))
    jdk = toolchains.registry()["jdk"]
    assert jdk.parts_for("linux-x86_64") and all(p["sha256"] for p in jdk.parts)
    monkeypatch.setattr(toolchains, "jdk_version", lambda: 25)
    assert ghidra.java_home() is None and ghidra._env().get("JAVA_HOME") == os.environ.get("JAVA_HOME")  # its own


@pytest.mark.skipif(os.name == "nt", reason="shell-script stand-ins for java")
@pytest.mark.parametrize("version, javac, own", [
    ("27", True, True),        # a JDK newer than Ghidra's 21 (a Mac's Temurin 27): its own, and Ghidra ran on it
    ("21.0.12", True, True),   # Ghidra's own minimum
    ("27", False, False),      # a Java 27 runtime alone: no javac, no JDK, so the pinned one
    ("17.0.9", True, False),   # a JDK too old for Ghidra 12: the pinned one
])
def test_ghidra_runs_on_the_computers_jdk_of_21_or_newer_and_never_a_runtime_alone(tc_root, monkeypatch, version,
                                                                                    javac, own):
    """Which Java Ghidra gets, from what is on PATH: the computer's own JDK, any version from 21 on, or else the
    pinned JDK (toolchains.toml `jdk`, Temurin 21), which is then needed (available()) and put first on Ghidra's
    PATH. A runtime alone, without javac, is no JDK: Ghidra's launcher quit on one."""
    from decompnet.forks import ghidra

    monkeypatch.setenv("PATH", str(_java(tc_root / "java", version, javac=javac)))
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr(toolchains, "installed", lambda tid, *args, **kw: {"id": tid} if tid == "ghidra" else None)
    env = ghidra._env()
    if own:
        assert ghidra.java_home() is None and ghidra.available()  # (the pinned JDK isn't installed, nor needed)
        assert "JAVA_HOME" not in env and env["PATH"] == str(tc_root / "java")
    else:
        assert ghidra.java_home() == toolchains.path("jdk") and not ghidra.available()  # (until the pinned one is)
        assert env["JAVA_HOME"] == str(toolchains.path("jdk")) and env["PATH"].startswith(str(toolchains.path("jdk") / "bin"))


@pytest.mark.skipif(os.name == "nt", reason="a shell-script stand-in for Ghidra")
def test_a_ghidra_that_didnt_run_says_why(tmp_path, monkeypatch):
    from decompnet.forks import ghidra
    from decompnet.forks.project import ForkError

    fake = tmp_path / "analyzeHeadless"
    fake.write_text("#!/bin/sh\necho 'ERROR: Unable to prompt user for JDK path, no TTY detected.'\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setattr(ghidra, "_headless", lambda: fake)
    monkeypatch.setattr(ghidra, "_env", lambda: None)
    monkeypatch.setattr(ghidra, "available", lambda: True)
    program = tmp_path / "game.exe"
    program.write_bytes(b"MZ" + bytes(64))
    with pytest.raises(ForkError, match="Unable to prompt user for JDK path.*Java development kit"):
        ghidra.functions(program)
    assert ghidra.decompile_object(program, tmp_path / "cache") == {}  # a draft is optional: none, nothing cached
    assert not (tmp_path / "cache").exists()


@pytest.mark.skipif(os.name == "nt", reason="the native Mac build needs POSIX executable permissions")
def test_intel_mac_linker_is_built_from_pinned_source(monkeypatch, tmp_path):
    """No Intel release asset exists: the pinned source yields executable MIPS tools, with no GDB dependency."""
    from types import SimpleNamespace

    dest = tmp_path / "tools"
    dest.mkdir()
    calls = []
    outputs = ["gas/as-new", "ld/ld-new", "binutils/objcopy", "binutils/objdump", "binutils/nm-new",
               "binutils/ar", "binutils/ranlib", "binutils/strip-new", "binutils/readelf"]
    def extract(archive, source, subdir):
        (source / "configure").touch()
        (source / "gdb").mkdir()
        (source / "gdbserver").mkdir()
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0].endswith("configure"):
            source = toolchains.Path(argv[0]).parent
            assert not (source / "gdb").exists() and not (source / "gdbserver").exists()
        else:
            for output in outputs:
                file = kwargs["cwd"] / output
                file.parent.mkdir(exist_ok=True)
                file.write_bytes(b"native host executable")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(toolchains.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(toolchains, "_extract", extract)
    monkeypatch.setattr(toolchains.limits, "capped", lambda argv: argv)
    monkeypatch.setattr(toolchains.subprocess, "run", run)
    toolchains._build_mips_binutils(tmp_path / "pinned.tar.gz", dest, "source", lambda _: None)
    assert {p.name for p in dest.iterdir()} >= {"mips-ps2-decompals-ld", "mips-ps2-decompals-objcopy", "mips-ps2-decompals-as"}
    assert all(p.stat().st_mode & 0o111 for p in dest.iterdir())
    assert len(calls) == 2 and all(kwargs["timeout"] == 900 for _, kwargs in calls)
    assert calls[0][1]["env"]["CC"] == "/usr/bin/clang"
    part = toolchains.get("binutils-mips").parts_for("macos-x86_64")[0]
    assert part["build"] == "mips-binutils" and len(part["sha256"]) == 64


def _fake_make(monkeypatch, returncode=0, said=""):
    """make standing in for the real one: it "builds" ghidra_opt where it's run, and says what it was asked."""
    from types import SimpleNamespace

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if not returncode:
            (kwargs["cwd"] / "ghidra_opt").write_bytes(b"native host executable")
        return SimpleNamespace(returncode=returncode, stdout="", stderr=said)
    monkeypatch.setattr(toolchains.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(toolchains.limits, "capped", lambda argv: argv)
    monkeypatch.setattr(toolchains.subprocess, "run", run)
    return calls


def _ghidra_source(tmp_path):
    ghidra = tmp_path / "ghidra"
    (ghidra / "Ghidra" / "Features" / "Decompiler" / "src" / "decompile" / "cpp").mkdir(parents=True)
    return ghidra


def test_ghidras_decompiler_is_built_where_its_release_has_none(monkeypatch, tmp_path):
    """Ghidra's release has its decompiler for Intel Linux and Windows only: on a Mac, Ghidra found the functions but
    not their calling conventions, so a __fastcall function was imported as __cdecl (_func_00401040 for
    @mix_score@8). There it's built from the release's own source, for this computer: the Makefile's Intel Mac
    (ARCH_TYPE=-arch x86_64) and macOS 10.6 (ADDITIONAL_FLAGS) are left out."""
    ghidra = _ghidra_source(tmp_path)
    calls = _fake_make(monkeypatch)
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-arm64")
    toolchains._build_ghidra_decompiler(ghidra, lambda _: None)
    built = ghidra / "Ghidra" / "Features" / "Decompiler" / "os" / "mac_arm_64" / "decompile"
    assert built.read_bytes() == b"native host executable"
    assert os.name == "nt" or built.stat().st_mode & 0o111  # (Windows has no executable bit: the CI runs this there too)
    (argv, kwargs), = calls
    assert argv[0] == "make" and argv[1].startswith("-j")
    assert argv[2:] == ["ghidra_opt", "ARCH_TYPE=", "ADDITIONAL_FLAGS=-w", "CXX=c++ -std=c++11"]
    assert kwargs["cwd"].name == "cpp" and ghidra not in kwargs["cwd"].parents  # built in a folder of its own
    assert not list((ghidra / "Ghidra" / "Features" / "Decompiler" / "src").rglob("ghidra_opt"))
    toolchains._build_ghidra_decompiler(ghidra, lambda _: None)  # one there already (the release's): kept
    assert len(calls) == 1
    ghidra_tc = toolchains.get("ghidra")
    for plat in ("macos-arm64", "macos-x86_64", "linux-aarch64"):
        assert [p.get("build") for p in ghidra_tc.parts_for(plat)] == ["ghidra-decompiler"], plat
    for plat in ("linux-x86_64", "windows-x86_64", "windows-aarch64"):  # the release's own (on Arm, emulated)
        assert [p.get("build") for p in ghidra_tc.parts_for(plat)] == [None], plat


@pytest.mark.parametrize("plat, builds", [("macos-arm64", True), ("macos-x86_64", True), ("linux-aarch64", True),
                                          ("linux-x86_64", False)])
def test_installing_ghidra_builds_its_decompiler_only_where_its_release_has_none(tc_root, monkeypatch, plat, builds):
    """install() itself: Ghidra's archive unpacked, then its decompiler built (or, with the release's own, not), and
    the install is installed() only with it. A test of the build alone passed with install() never calling it."""
    import zipfile

    monkeypatch.setattr(toolchains, "current_platform", lambda: plat)
    monkeypatch.setattr(toolchains, "jdk_version", lambda: 21)
    part = toolchains.get("ghidra").parts_for()[0]
    downloads = toolchains.root() / ".downloads"
    downloads.mkdir(parents=True)
    top = part["subdir"] + "/Ghidra/Features/Decompiler"
    with zipfile.ZipFile(downloads / toolchains.Path(part["url"]).name, "w") as z:
        z.writestr(top + "/src/decompile/cpp/Makefile", "")
        z.writestr(top + "/os/linux_x86_64/decompile", "the release's")
    monkeypatch.setattr(toolchains, "_sha256", lambda _: part["sha256"])
    monkeypatch.setattr(toolchains, "_download", lambda *a: pytest.fail("nothing to download"))
    calls = _fake_make(monkeypatch)
    toolchains.install("ghidra", log=lambda _: None)
    assert len(calls) == builds and toolchains.installed("ghidra") is not None
    decompiler = toolchains.ghidra_decompiler(toolchains.path("ghidra"))
    assert decompiler.read_bytes() == (b"native host executable" if builds else b"the release's")


def test_a_ghidra_decompiler_build_without_build_tools_says_what_to_install(monkeypatch, tmp_path):
    ghidra = _ghidra_source(tmp_path)
    monkeypatch.setattr(toolchains.shutil, "which", lambda name: None)
    monkeypatch.setattr(toolchains.subprocess, "run", lambda *a, **kw: pytest.fail("nothing to run"))
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-arm64")
    with pytest.raises(toolchains.ToolchainError, match="needs Xcode Command Line Tools: run `xcode-select --install`"):
        toolchains._build_ghidra_decompiler(ghidra, lambda _: None)
    monkeypatch.setattr(toolchains, "current_platform", lambda: "linux-aarch64")
    with pytest.raises(toolchains.ToolchainError, match="needs a C\\+\\+ compiler and make"):
        toolchains._build_ghidra_decompiler(ghidra, lambda _: None)


def test_a_failed_ghidra_decompiler_build_says_why_and_leaves_none(monkeypatch, tmp_path):
    """The build's own words, and no decompiler: the install is taken back (install()), and tried again next time."""
    ghidra = _ghidra_source(tmp_path)
    _fake_make(monkeypatch, returncode=2, said="fatal error: 'zlib.h' file not found")
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-arm64")
    with pytest.raises(toolchains.ToolchainError, match="(?s)Couldn't build Ghidra's decompiler.*'zlib.h' file not found"):
        toolchains._build_ghidra_decompiler(ghidra, lambda _: None)
    assert not toolchains.ghidra_decompiler(ghidra).exists()


def test_a_ghidra_installed_without_its_decompiler_is_installed_again(tc_root, monkeypatch):
    """A Mac's Ghidra installed before its decompiler was built would go on analysing without it: it isn't installed,
    so it's installed again (ensure), and the build that comes with it gives the decompiler. Its manifest, from before
    installs recorded a fingerprint, is judged by its archive, which is the same (changed()): that alone keeps it. On
    every computer, not only where it's built: a Ghidra whose decompiler went missing is installed again too."""
    where = toolchains.path("ghidra")
    where.mkdir(parents=True)
    part = toolchains.get("ghidra").parts_for("macos-arm64")[0]
    old = {"id": "ghidra", "parts": [{"url": part["url"], "sha256": part["sha256"]}], "commit": None}
    for plat in ("macos-arm64", "macos-x86_64", "linux-aarch64", "linux-x86_64", "windows-x86_64", "windows-aarch64"):
        monkeypatch.setattr(toolchains, "current_platform", lambda plat=plat: plat)
        (where / toolchains.MANIFEST).write_text(json.dumps(old))  # (the same install, made on this computer)
        assert not toolchains.changed("ghidra", old) and toolchains.installed("ghidra") is None, plat
        decompiler = toolchains.ghidra_decompiler(where)
        decompiler.parent.mkdir(parents=True, exist_ok=True)
        decompiler.write_bytes(b"")
        assert toolchains.installed("ghidra")["parts"] == old["parts"], plat
        decompiler.unlink()


def test_source_build_failure_reports_the_build_output(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setattr(toolchains.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(toolchains, "_extract", lambda *args: None)
    monkeypatch.setattr(toolchains.limits, "capped", lambda argv: argv)
    monkeypatch.setattr(toolchains.subprocess, "run", lambda *args, **kw: SimpleNamespace(
        returncode=1, stdout="", stderr="C compiler cannot create executables"))
    with pytest.raises(toolchains.ToolchainError, match="C compiler cannot create executables"):
        toolchains._build_mips_binutils(tmp_path / "source.tar.gz", tmp_path, None, lambda _: None)


def test_source_build_reports_a_missing_binary(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setattr(toolchains.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(toolchains, "_extract", lambda *args: None)
    monkeypatch.setattr(toolchains.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    with pytest.raises(toolchains.ToolchainError, match="did not produce as"):
        toolchains._build_mips_binutils(tmp_path / "source.tar.gz", tmp_path, None, lambda _: None)


def test_a_ghidra_without_its_decompiler_isnt_run(tc_root, monkeypatch):
    """The review's repro: a Ghidra installed without its decompiler (a manifest, no os/<platform>/decompile) isn't
    installed and isn't available(), but ghidra.functions ran it anyway, and every function's convention came back
    "unknown". It isn't run: the import stops and says to install it again."""
    from decompnet.forks import ghidra
    from decompnet.forks.project import ForkError

    monkeypatch.setattr(toolchains, "jdk_version", lambda: 21)
    where = toolchains.path("ghidra")
    (where / "support").mkdir(parents=True)
    part = toolchains.get("ghidra").parts_for()[0]
    (where / toolchains.MANIFEST).write_text(json.dumps({"id": "ghidra", "parts": [
        {"url": part["url"], "sha256": part["sha256"]}], "commit": None}))
    assert toolchains.installed("ghidra") is None and not ghidra.available()
    monkeypatch.setattr(ghidra.subprocess, "run", lambda *a, **kw: pytest.fail("Ghidra was run"))
    program = tc_root / "game.exe"
    program.write_bytes(b"MZ" + bytes(64))
    with pytest.raises(ForkError, match="Ghidra isn't installed, or not all of it .*decompdecomp installs it"):
        ghidra.functions(program)


def test_ghidras_java_heap_is_half_its_memory_cap(monkeypatch):
    """analyzeHeadless gives Java 2 GB whatever the cap: a bigger game's analysis gets half the cap instead, and the
    other half is left to the decompiler processes it starts. A heap the user chose stays."""
    from decompnet.forks import ghidra

    monkeypatch.delenv("GHIDRA_MAXMEM", raising=False)
    monkeypatch.delenv("GHIDRA_HEADLESS_MAXMEM", raising=False)
    monkeypatch.setattr(ghidra, "default_cap", lambda: 8 << 30)
    assert ghidra._env()["GHIDRA_HEADLESS_MAXMEM"] == "4096M"
    monkeypatch.setattr(ghidra, "default_cap", lambda: 2 << 30)
    assert ghidra._env()["GHIDRA_HEADLESS_MAXMEM"] == "2048M"  # never less than its own
    monkeypatch.setenv("GHIDRA_MAXMEM", "12G")
    assert "GHIDRA_HEADLESS_MAXMEM" not in ghidra._env()


@pytest.mark.skipif(os.name == "nt", reason="shell-script stand-ins for Ghidra")
@pytest.mark.parametrize("dies", ["kill -9 $$", "echo 'Exception in thread main java.lang.OutOfMemoryError: Java heap "
                                               "space' >&2; exit 1"])
def test_a_ghidra_out_of_memory_says_so(tmp_path, monkeypatch, dies):
    """A Ghidra stopped at its memory cap said only "Ghidra didn't run: WARNING: sun.misc.Unsafe...", Java's last
    line at its start."""
    from decompnet.forks import ghidra
    from decompnet.forks.project import ForkError

    fake = tmp_path / "analyzeHeadless"
    fake.write_text(f"#!/bin/sh\necho 'WARNING: sun.misc.Unsafe::staticFieldOffset has been called' >&2\n{dies}\n")
    fake.chmod(0o755)
    monkeypatch.setattr(ghidra, "_headless", lambda: fake)
    monkeypatch.setattr(ghidra, "capped", lambda argv, **_: argv)
    monkeypatch.setattr(ghidra, "available", lambda: True)
    program = tmp_path / "game.exe"
    program.write_bytes(b"MZ" + bytes(64))
    with pytest.raises(ForkError, match="Ghidra ran out of memory analysing game.exe: .* GB decompdecomp lets one tool"):
        ghidra.functions(program)


@pytest.mark.skipif(limits._systemd_run() is None, reason="needs systemd-run --user (a Linux desktop session)")
def test_a_process_killed_at_the_cap_stops_its_command_unless_it_copes():
    """Over the cap the kernel kills the biggest process; systemd then stopped the rest of the command, so Ghidra
    died with its runaway decompiler process (6 GB on a 6 MB game) and never said why. A tool that copes keeps
    going (stop_all=False), still capped."""
    parent = ("import subprocess, sys, time\n"
              "child = subprocess.run([sys.executable, '-c', 'x = bytearray(400 << 20); import time; time.sleep(5)'])\n"
              "time.sleep(2)\nprint('carried on after', child.returncode)\n")
    cap = 200 << 20
    kept = subprocess.run(limits.capped([sys.executable, "-c", parent], cap, stop_all=False), capture_output=True,
                          text=True, timeout=60)
    assert kept.returncode == 0 and "carried on after -9" in kept.stdout
    stopped = subprocess.run(limits.capped([sys.executable, "-c", parent], cap), capture_output=True, text=True,
                             timeout=60)
    assert stopped.returncode != 0 and "carried on" not in stopped.stdout


def _archive(tc_root, name: str, files: dict[str, str]) -> tuple[str, str]:
    """A local .tar.gz of `files` ({path in it: text}): its file:// address and sha256."""
    archive = tc_root / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        for inside, text in files.items():
            src = tc_root / "files" / name / inside
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(text)
            t.add(src, arcname=inside)
    return archive.as_uri(), hashlib.sha256(archive.read_bytes()).hexdigest()


def _pack_and_sdk(monkeypatch, tc_root) -> dict:
    """A compiler pack of two versions that names an SDK of its own in `headers`, as msvc2015 does."""
    url, sha = _archive(tc_root, "pack", {"bin/cl.exe": "cl", "include/sal.h": "the compiler's"})
    sdk_url, sdk_sha = _archive(tc_root, "sdk", {"um/windows.h": "windows", "shared/sal.h": "the SDK's"})
    reg = {"sdk": toolchains.Toolchain(id="sdk", name="SDK", license="proprietary", source="test", parts=[
               {"url": sdk_url, "sha256": sdk_sha, "keep": {"um": "Include", "shared": "Include"}}]),
           "pack": toolchains.Toolchain(id="pack", name="Pack", license="proprietary", source="test", headers=["sdk"],
                                        parts=[{"url": url, "sha256": sha, "keep": {"bin": "Bin", "include": "Include"},
                                                "into": version} for version in ("1.0", "1.0-x64")])}
    monkeypatch.setattr(toolchains, "registry", lambda: reg)
    original_registered = toolchains._registered
    monkeypatch.setattr(toolchains, '_registered', lambda tid: toolchains.registry().get(tid) or original_registered(tid))
    return reg


def test_a_pack_installs_the_sdk_it_names_once_and_each_version_says_where_it_is(tc_root, monkeypatch):
    """A Windows SDK is a toolchain of its own that packs name in `headers`: installed first, once, and each of the
    pack's versions says where its headers are, relative to the version's folder (the toolchains' folder can move),
    searched after the compiler's own Include. Without the SDK the pack isn't installed: it can't build."""
    reg = _pack_and_sdk(monkeypatch, tc_root)
    assert reg["pack"].requires == ["sdk"]
    toolchains.install("pack", accept_license=True, log=lambda *_: None)
    sdk = toolchains.path("sdk")
    assert (sdk / "Include" / "windows.h").exists() and toolchains.installed("sdk")["license_accepted"]
    for version in ("1.0", "1.0-x64"):
        folder = toolchains.path("pack") / version
        assert (folder / toolchains.INCLUDES).read_text() == "../../sdk/Include\n"
        assert (folder / "Include" / "sal.h").read_text() == "the compiler's"  # (the SDK's isn't merged in)
        assert (folder / "../../sdk/Include/windows.h").resolve() == (sdk / "Include" / "windows.h").resolve()
    shutil.rmtree(sdk)
    assert toolchains.installed("pack") is None
    toolchains.install("pack", accept_license=True, log=lambda *_: None)  # (only the SDK comes back)
    assert toolchains.installed("pack") is not None and toolchains.installed("sdk") is not None


def test_an_install_from_an_entry_that_changed_is_offered_again(tc_root, monkeypatch):
    """When a pack's entry changes (here: where an archive's folders go), its install isn't the pack any more: it
    counts as not installed, Compilers & tools says it changed, and installing it again takes the archives already
    downloaded. A change to its name or description doesn't count."""
    reg = _pack_and_sdk(monkeypatch, tc_root)
    toolchains.install("pack", accept_license=True, log=lambda *_: None)
    m = toolchains.installed("pack")
    assert m["fingerprint"] == toolchains.fingerprint(reg["pack"])
    reg["pack"].description = "said another way"
    assert toolchains.installed("pack") is not None
    reg["pack"].parts[1]["keep"] = {"bin": "Bin", "include": "Include", "bin/cl.exe": "Bin/cl64.exe"}
    assert toolchains.installed("pack") is None
    assert {r["id"]: r["changed"] for r in toolchains.status()} == {"pack": True, "sdk": False}
    monkeypatch.setattr(toolchains, "_download", lambda *a: pytest.fail("downloaded again"))
    with pytest.raises(toolchains.ToolchainError, match="--accept-license"):
        toolchains.install("pack", log=lambda *_: None)  # (proprietary: installing again is accepting again)
    toolchains.install("pack", accept_license=True, log=lambda *_: None)
    assert (toolchains.path("pack") / "1.0-x64" / "Bin" / "cl64.exe").exists() and toolchains.installed("pack")
    assert not any(r["changed"] for r in toolchains.status())


def test_an_existing_windows_ido_install_is_offered_again_for_the_runtime(tc_root, monkeypatch):
    """#130: a Windows install from the old entry has only the release archive, so it has no msys-2.0.dll. The entry
    now has the runtime part: installed() sees the change, and Compilers & tools offers Install again."""
    monkeypatch.setattr(toolchains, "current_platform", lambda: "windows-x86_64")
    tc = toolchains.get("ido-7.1")
    old = [{"url": p["url"], "sha256": p["sha256"]} for p in tc.parts_for()
           if p.get("keep", {}).get("usr/bin/msys-2.0.dll") != "msys-2.0.dll"]
    folder = toolchains.path(tc.id)
    folder.mkdir(parents=True)
    (folder / toolchains.MANIFEST).write_text(json.dumps({"id": tc.id, "parts": old, "commit": None, "wsl": False}))
    assert toolchains.installed(tc.id) is None
    assert {r["id"]: r["changed"] for r in toolchains.status()}[tc.id] is True


def test_an_install_made_before_fingerprints_is_judged_by_its_archives(tc_root):
    """Installs made before manifests had a fingerprint record their archives: one whose archives are still the
    entry's is current (and is given the fingerprint), so updating decompdecomp reinstalls nothing that didn't change;
    one with an archive the entry no longer has isn't. Checked for every toolchain the registry has."""
    for tc in toolchains.registry().values():
        for linux in (False, True):
            parts = tc.linux_parts() if linux else tc.parts_for()
            if not parts:
                continue
            pinned = [{"url": p["url"], "sha256": p.get("sha256") or "f" * 64} for p in parts]
            pinned += [{"url": p[k], "sha256": p[k.removesuffix("_url") + "_sha256"]}
                       for p in parts for k in p if k.endswith("_url")]
            folder = toolchains.path(tc.id, linux)
            folder.mkdir(parents=True, exist_ok=True)
            manifest = {"id": tc.id, "parts": pinned, "commit": tc.commit or None, "wsl": linux}
            (folder / toolchains.MANIFEST).write_text(json.dumps(manifest))
            assert not toolchains.changed(tc.id, manifest, folder), tc.id
            assert json.loads((folder / toolchains.MANIFEST).read_text())["fingerprint"] == \
                toolchains.fingerprint(tc, linux), tc.id
            assert toolchains.changed(tc.id, {**manifest, "parts": [*pinned, {"url": "https://x/old.tar.gz",
                                                                            "sha256": "0" * 64}]}), tc.id
    sdk = toolchains.get("msvc2015")  # an install from before the SDK was a toolchain of its own: install it again
    old = [{"url": url, "sha256": "0" * 64} for url in ("https://github.com/ojdkbuild/tools_toolchain_sdk10_17763/"
           "archive/a647d1194cd10afdaa52f8056db239b7598fe1dd.tar.gz", sdk.parts[0]["url"])]
    assert toolchains.changed("msvc2015", {"parts": old, "commit": None, "wsl": False})


def test_a_manifest_is_never_seen_half_written(tc_root, monkeypatch):
    """installed() can add the fingerprint to an older manifest while /api/state and builds read it: it's written to
    a file beside it and moved over it in one step, so a reader finds the old one or the new one, and nothing else
    is left in the folder."""
    tc = toolchains.get("wibo")
    folder = toolchains.path("wibo")
    folder.mkdir(parents=True)
    parts = [{"url": p["url"], "sha256": p["sha256"]} for p in tc.parts_for()]
    (folder / toolchains.MANIFEST).write_text(json.dumps({"id": "wibo", "parts": parts, "commit": None, "wsl": False}))
    moved = []
    real = os.replace
    name = os.path.basename
    monkeypatch.setattr(toolchains.os, "replace", lambda a, b: (moved.append((name(a), name(b))), real(a, b)))
    assert toolchains.installed("wibo") is not None
    assert moved and moved[0][1] == toolchains.MANIFEST and moved[0][0] != toolchains.MANIFEST
    assert json.loads((folder / toolchains.MANIFEST).read_text())["fingerprint"] == toolchains.fingerprint(tc, False)
    assert sorted(f.name for f in folder.iterdir()) == [toolchains.MANIFEST]


def test_threads_that_all_need_a_free_tool_install_it_once(tc_root, monkeypatch):
    """When an update changed objdiff's entry, a project's indexing threads each found it not installed and each
    started downloading it into the same file, and each then found the others' bytes in it (checksum mismatch):
    one install at a time, and the others find it installed."""
    import threading
    import time

    _fake(monkeypatch, tc_root, sha=None)
    downloads, real = [], toolchains._download

    def slow(url, dest, log):
        downloads.append(url)
        time.sleep(0.2)  # (long enough for every thread to ask before the first is done)
        real(url, dest, log)

    monkeypatch.setattr(toolchains, "_download", slow)
    errors = []

    def need():
        try:
            toolchains.ensure("fake", log=lambda *_: None)
        except Exception as error:  # noqa: BLE001 (any of them is the failure)
            errors.append(error)

    threads = [threading.Thread(target=need) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(downloads) == 1 and toolchains.installed("fake") is not None
    assert not list((toolchains.root() / ".downloads").glob("*.part"))  # (written beside it, then moved whole)


@pytest.mark.parametrize('platform', ['macos-arm64', 'macos-x86_64'])
def test_wibo_is_supported_and_visible_as_a_mac_tool(tc_root, monkeypatch, platform):
    from decompnet.app.engine import TOOLS
    monkeypatch.setattr(toolchains, 'current_platform', lambda: platform)
    assert 'wibo' in TOOLS
    row = next(t for t in toolchains.status() if t['id'] == 'wibo')
    assert row['supported'] and not row['installed'] and row['kind'] == 'tool'
    with pytest.raises(toolchains.ToolchainError, match='Compilers & tools → Tools'):
        toolchains.require('wibo')


def test_install_repairs_missing_runtime_without_reinstalling_compiler(tc_root, monkeypatch):
    url, sha = _archive(tc_root, 'runtime', {'runner':'test runtime'})
    compiler_url, compiler_sha = _archive(tc_root, 'compiler', {'compiler':'test compiler'})
    reg = {
        'runner':toolchains.Toolchain(id='runner',name='Runner',license='MIT',source='test',kind='tool',
                                     parts=[{'url':url,'sha256':sha}]),
        'compiler':toolchains.Toolchain(id='compiler',name='Compiler',license='MIT',source='test',requires=['runner'],
                                       parts=[{'url':compiler_url,'sha256':compiler_sha}]),
    }
    monkeypatch.setattr(toolchains, 'registry', lambda: reg)
    original_registered = toolchains._registered
    monkeypatch.setattr(toolchains, '_registered', lambda tid: toolchains.registry().get(tid) or original_registered(tid))
    toolchains.install('compiler', log=lambda _:None)
    compiler = toolchains.path('compiler')
    stamp = (compiler/toolchains.MANIFEST).stat().st_mtime_ns
    shutil.rmtree(toolchains.path('runner'))
    row = next(t for t in toolchains.status() if t['id'] == 'compiler')
    assert row['installed'] and row['missing_dependencies'] == [{'id':'runner','name':'Runner'}]
    toolchains.install('compiler', log=lambda _:None)
    assert (compiler/toolchains.MANIFEST).stat().st_mtime_ns == stamp
    assert toolchains.require('runner').is_dir()
    assert len(toolchains.runtime_dirs('compiler')) == 2
    assert next(t for t in toolchains.status() if t['id'] == 'compiler')['missing_dependencies'] == []


def test_single_toolchain_lookup_constructs_only_the_requested_entry(monkeypatch):
    original = toolchains.Toolchain
    made = []
    def construct(**fields):
        made.append(fields['id'])
        return original(**fields)
    monkeypatch.setattr(toolchains, 'Toolchain', construct)
    monkeypatch.setattr(toolchains, 'registry', lambda: pytest.fail('single lookup rebuilt the full catalogue'))
    assert toolchains.get('gcc-2.7.2-kmc').id == 'gcc-2.7.2-kmc'
    assert made == ['gcc-2.7.2-kmc']


def test_single_toolchain_lookup_keeps_metadata_fresh(monkeypatch):
    spec = dict(name='First', license='MIT', source='synthetic', flags=['-O1'])
    monkeypatch.setattr(toolchains, '_registry_data', lambda: {'synthetic':spec})
    monkeypatch.setattr(toolchains, '_own', lambda tid: None)
    first = toolchains.get('synthetic')
    first.name = 'caller edit'
    spec['name'] = 'Updated'
    second = toolchains.get('synthetic')
    assert second is not first and second.name == 'Updated'
    assert second.flags == ['-O1']
    with pytest.raises(toolchains.ToolchainError, match='unknown toolchain'):
        toolchains.get('missing')
