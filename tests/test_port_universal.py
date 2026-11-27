"""The Mac app export is universal (port/wasm/build.py, `universal`): an app exported on an Apple Silicon Mac runs on
an Intel Mac too, and the other way round. Play's own build stays for this computer only."""

import inspect
import shutil
import subprocess
import sys

import pytest

from decompnet.port.wasm import build, export


def test_only_a_universal_build_on_macos_gets_both_architectures():
    assert build.host_arch_flags(True, "darwin") == ["-arch", "arm64", "-arch", "x86_64"]
    assert build.host_arch_flags(False, "darwin") == []
    assert build.host_arch_flags(True, "linux") == []
    assert build.host_arch_flags(True, "win32") == []


def test_the_app_export_asks_for_a_universal_build_and_play_does_not(tmp_path, monkeypatch):
    from decompnet.port.wasm import play
    repo, program, asked = tmp_path / "save", tmp_path / "game", {}
    repo.mkdir()
    program.write_bytes(b"\xcf\xfa\xed\xfe")
    monkeypatch.setattr(play, "game", lambda net, slug: (repo, {"platform": "n64", "rom": {"title": "RALLY"}}, None))
    monkeypatch.setattr(play, "library", lambda net, slug, log=print: set())
    monkeypatch.setattr(play, "unverified", lambda net, slug, log=print: set())  # (the save is whole)
    monkeypatch.setattr(play, "library_c", lambda net, slug: {})  # (no ultralib units: port/ultralib.py)
    monkeypatch.setattr(play, "library_names", lambda net, slug: {})  # (no library names to rename)
    monkeypatch.setattr(play, "relative_bank_code", lambda net, slug, log=print: set())  # (no banked overlays)
    monkeypatch.setattr(play, "recompiled", lambda net, slug, log=print: None)  # (no recompiled C)
    monkeypatch.setattr(play, "function_headers", lambda net, slug: {})  # (headers as the save names them)
    monkeypatch.setattr(play, "code_offsets", lambda net, slug: None)  # (no project image in this fixture)
    monkeypatch.setattr(build, "build", lambda *a, **k: asked.update(k) or program)
    export.export(None, "rally", "app", log=lambda *a: None)
    assert asked["universal"] is True
    assert "universal=True" not in inspect.getsource(play)  # Play builds for this Mac only


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("xcrun") is None or shutil.which("lipo") is None,
                    reason="needs macOS with Xcode's Command Line Tools")
def test_the_host_compiler_with_the_universal_flags_makes_one_program_for_both_macs(tmp_path):
    """What build.py runs for the host, on a small program: one file holding arm64 and x86_64."""
    source = tmp_path / "hello.c"
    source.write_text("#include <pthread.h>\nint main(void) { return pthread_self() == 0; }\n")
    cc = [*build.host_compiler(None), *build.host_arch_flags(True)]
    obj, program = tmp_path / "hello.o", tmp_path / "hello"
    for argv in ([*cc, "-O1", "-c", str(source), "-o", str(obj)], [*cc, "-o", str(program), str(obj), "-lpthread"]):
        done = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr
    archs = subprocess.run(["lipo", "-archs", str(program)], capture_output=True, text=True).stdout.split()
    assert sorted(archs) == ["arm64", "x86_64"]
