import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile

import pytest

from decompnet import limits, toolchains


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


@pytest.mark.skipif(os.name == "nt", reason="a shell-script stand-in for Ghidra")
def test_a_ghidra_that_didnt_run_says_why(tmp_path, monkeypatch):
    from decompnet.forks import ghidra
    from decompnet.forks.project import ForkError

    fake = tmp_path / "analyzeHeadless"
    fake.write_text("#!/bin/sh\necho 'ERROR: Unable to prompt user for JDK path, no TTY detected.'\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setattr(ghidra, "_headless", lambda: fake)
    monkeypatch.setattr(ghidra, "_env", lambda: None)
    program = tmp_path / "game.exe"
    program.write_bytes(b"MZ" + bytes(64))
    with pytest.raises(ForkError, match="Unable to prompt user for JDK path.*Java development kit"):
        ghidra.functions(program)
    monkeypatch.setattr(ghidra, "available", lambda: True)
    assert ghidra.decompile_object(program, tmp_path / "cache") == {}  # a draft is optional: none, nothing cached
    assert not (tmp_path / "cache").exists()


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
