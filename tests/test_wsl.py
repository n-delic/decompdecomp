"""WSL compiler staging: complete tool caches, fresh installs, quoted paths, and real 32-bit compiles when installed."""

import shlex
import subprocess
import sys

import pytest

from decompnet import toolchains, wsl
from decompnet.compiler import compile_source
from decompnet.elfobj import extract_function
from decompnet.project import compiler_config


@pytest.fixture
def tool_cache(tmp_path, monkeypatch):
    directories = tuple(tmp_path / name for name in ("compiler's tools", "runtime-i386"))
    for directory in directories:
        directory.mkdir()
        (directory / toolchains.MANIFEST).write_text('{"version":1}')
    monkeypatch.setattr(wsl, "_prepared_tools", set())
    monkeypatch.setattr(wsl, "_cache_directory", lambda: "/home/test/.cache/decompnet/toolchains")
    monkeypatch.setattr(wsl, "path", str)
    calls = []

    def run(argv, cwd, timeout):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(wsl, "run", run)
    return directories, calls


def test_parallel_compiles_share_one_complete_linux_tool_cache(tool_cache):
    from concurrent.futures import ThreadPoolExecutor

    directories, calls = tool_cache
    with ThreadPoolExecutor(4) as pool:
        paths = list(pool.map(lambda _: wsl.prepare_tools(directories), range(8)))
    assert len(calls) == 1 and all(path == paths[0] for path in paths)
    assert all(path.startswith("/home/test/.cache/decompnet/toolchains/") for path in paths[0])
    script = calls[0][-1]
    assert f"cp -R {shlex.quote(str(directories[0]))}" in script
    assert 'mv -T "$stage"' in script and '/.ready' in script


def test_reinstalling_a_compiler_gets_a_fresh_linux_copy(tool_cache):
    directories, calls = tool_cache
    first = wsl.prepare_tools(directories)
    (directories[0] / toolchains.MANIFEST).write_text('{"version":2}')
    assert wsl.prepare_tools(directories) != first and len(calls) == 2


def test_a_failed_linux_copy_is_not_cached(tool_cache, monkeypatch):
    directories, _ = tool_cache
    calls = []

    def failed(argv, *args):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "no space left")
    monkeypatch.setattr(wsl, "run", failed)
    for _ in range(2):
        with pytest.raises(OSError, match="no space left"):
            wsl.prepare_tools(directories)
    assert len(calls) == 2 and not wsl._prepared_tools


def test_linux_keeps_using_the_original_pinned_compiler(monkeypatch, tmp_path):
    monkeypatch.setattr(toolchains.sys, "platform", "linux")
    monkeypatch.delenv("DECOMP_WSL", raising=False)
    monkeypatch.setattr(toolchains, "runtime_dirs", lambda _: (tmp_path,))
    monkeypatch.setattr(wsl, "prepare_tools", lambda *_: pytest.fail("a native Linux compiler was copied to WSL"))
    cfg = compiler_config(tmp_path, {"compiler": {"toolchain": "gcc-2.7.2-kmc"}})
    assert not cfg.wsl and cfg.cmd[0].startswith(str(tmp_path))


def test_windows_compiles_headers_from_paths_with_spaces_and_unicode(tmp_path):
    if sys.platform != "win32" or toolchains.installed("gcc-2.7.2-kmc") is None:
        pytest.skip("needs the pinned N64 compiler on Windows and WSL 2")
    if not wsl.status()[0]:
        pytest.skip("needs a working WSL 2 distribution")
    headers = tmp_path / "headers with spaces ü"
    headers.mkdir()
    (headers / "extra.h").write_bytes(b'#define ANSWER \\\r\n7\r\n')
    cfg = compiler_config(tmp_path, {"compiler": {"toolchain": "gcc-2.7.2-kmc"}})
    cfg.include_dirs = (str(headers),)
    result = compile_source(cfg, '#include "extra.h"\n', 'int example(void) { return ANSWER; }')
    plain = compile_source(cfg, '', 'int example(void) { return 7; }')
    assert result.ok and plain.ok, result.log + plain.log
    assert extract_function(result.obj, "example").code == extract_function(plain.obj, "example").code
