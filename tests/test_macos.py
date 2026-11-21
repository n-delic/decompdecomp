"""Mac integration points, with isolated launchers and no dialogs or real agents in the regular test run."""

import functools
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from decompnet import hostenv, linuxvm, toolchains
from decompnet.app import agents as app_agents
from decompnet.app import launcher, macwindow, picker, updates, window


@pytest.mark.parametrize("url,kind", [
    ("http://127.0.0.1:6474/api/export?file=1", "internal"),
    ("http://127.0.0.1:64740/", "external"),
    ("http://127.0.0.1:6474.evil.example/", "external"),
    ("https://example.com/", "external"), ("https://127.0.0.1:6474/", "external"),
    ("javascript:alert(1)", "reject"), ("file:///etc/passwd", "reject"), ("data:text/html,hello", "reject"),
])
def test_native_navigation_keeps_the_private_origin(url, kind):
    assert macwindow.navigation_kind("http://127.0.0.1:6474/", url) == kind


def test_only_local_exports_can_download():
    origin = "http://127.0.0.1:6474/"
    assert macwindow.export_url(origin, origin + "api/export")
    assert macwindow.export_url(origin, "blob:" + origin + "uuid")
    assert not macwindow.export_url(origin, "blob:http://127.0.0.1:64740/uuid")
    assert not macwindow.export_url(origin, "https://other.example/export")


def test_missing_pyobjc_still_shows_a_finder_launch_error(monkeypatch):
    monkeypatch.setattr(window, "IS_MACOS", True)
    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setitem(sys.modules, "AppKit", None)
    calls = []
    monkeypatch.setattr(window.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    window.tell_desktop("decompdecomp can't open", "Install PyObjC")
    assert calls and calls[0][0] == "/usr/bin/osascript"
    assert calls[0][-2:] == ["decompdecomp can't open", "Install PyObjC"]


@pytest.mark.skipif(sys.platform != "darwin", reason="uses macOS ps process metadata")
def test_mac_finds_detached_helpers_even_with_spaces_in_home(tmp_path):
    manager = object.__new__(app_agents.Manager)
    manager.home = tmp_path / "space home"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            env=dict(os.environ, **{app_agents.HELPER_ENV: str(manager.home)}), start_new_session=True)
    try:
        assert app_agents._proc_start(proc.pid) is not None
        assert os.getpgid(proc.pid) in manager._strays()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.parametrize("cpu", ["x86_64", "arm64"])
def test_mac_hosts_use_pinned_tools(cpu, monkeypatch):
    monkeypatch.setattr(linuxvm, "wanted", lambda: True)
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-" + cpu)
    monkeypatch.setattr(toolchains.wsl, "wanted", lambda: False)
    assert toolchains.unsupported_cpu() is None
    assert toolchains.get("gcc-2.7.2-kmc").supported
    assert toolchains.get("ee-gcc-2.96").supported
    assert toolchains.get("ee-gcc-2.96").via_vm
    parts = toolchains.get("gcc-2.7.2-kmc").parts_for()
    assert parts and all(part["sha256"] for part in parts)
    if cpu == "arm64":
        assert all(part.get("build") and not part.get("rosetta") for part in parts)
    else:
        assert all(part.get("rosetta") for part in parts)


def test_missing_rosetta_is_an_actionable_error_before_download(monkeypatch, tmp_path):
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-arm64")
    monkeypatch.setenv("DECOMPNET_TOOLCHAINS", str(tmp_path))
    downloads = []
    monkeypatch.setattr(toolchains, "_download", lambda *a, **kw: downloads.append(a))
    monkeypatch.setattr(toolchains.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(toolchains.ToolchainError, match="softwareupdate --install-rosetta"):
        toolchains.install("gcc-egcs-2.91.66")
    assert not list(tmp_path.iterdir())
    assert not downloads
    monkeypatch.setattr(toolchains.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    (tmp_path / "gcc-egcs-2.91.66").mkdir()
    (tmp_path / "gcc-egcs-2.91.66" / toolchains.MANIFEST).write_text("{}")
    assert toolchains.require("gcc-egcs-2.91.66") == tmp_path / "gcc-egcs-2.91.66"
    toolchains._intel_mac_runtime_ready.cache_clear()


def test_finder_launcher_preserves_interpreter_and_helper_path(monkeypatch, tmp_path):
    home = tmp_path / "Owner's Mac"
    monkeypatch.setattr(launcher.Path, "home", lambda: home)
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher.sys, "executable", str(home / "Python env/bin/python"))
    monkeypatch.setattr(launcher.edition, "DEV", False)
    monkeypatch.setenv("PATH", str(home / "node bin") + ":/usr/bin:/bin")
    launcher.install_launcher()
    bundle = home / "Applications/decompdecomp.app/Contents"
    info = plistlib.loads((bundle / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "com.ndelic.decompdecomp"
    icon = bundle / "Resources" / info["CFBundleIconFile"]
    assert icon.read_bytes() == (Path(launcher.__file__).with_name("static") / "icon.icns").read_bytes()
    assert icon.read_bytes()[:4] == b"icns"
    script = bundle / "MacOS" / info["CFBundleExecutable"]
    if os.name != "nt":
        assert script.stat().st_mode & 0o111
    lines = script.read_text().splitlines()
    assert shlex.split(lines[1]) == ["export", "PATH=" + os.environ["PATH"]]
    assert shlex.split(lines[2]) == ["exec", sys.executable, "-m", "decompnet.app.cli", "$@"]


def test_packaged_mac_app_keeps_its_own_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher.edition, "DEV", False)
    monkeypatch.setenv("DECOMP_PACKAGE", "macos")
    assert "already available" in launcher.install_launcher()
    assert not (tmp_path / "Applications").exists()
    assert updates.can_apply() == (False, "this decompdecomp was installed as a macOS app: download the new one from https://github.com/RetroReversing/decompdecomp/releases")


def test_mac_chooser_uses_the_apps_python_and_cancellation_is_empty(monkeypatch):
    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setattr(window, "IS_MACOS", True)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="/Users/owner/Decomp save\n")
    monkeypatch.setattr(picker.subprocess, "run", run)
    assert picker.choose("folder", "Open a save") == "/Users/owner/Decomp save"
    assert calls[0][:2] == [sys.executable, "-I"]
    monkeypatch.setattr(picker.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=""))
    assert picker.choose("file", "Open") == ""


def test_mac_opens_folders_in_finder(monkeypatch, tmp_path):
    monkeypatch.setattr(hostenv.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(hostenv.subprocess, "Popen", lambda argv, **kwargs: calls.append(argv))
    assert hostenv.open_folder(tmp_path) is None
    assert calls == [["open", str(tmp_path)]]


def test_mac_window_needs_no_x11_display(monkeypatch):
    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setattr(window, "IS_MACOS", True)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(window.importlib.util, "find_spec", lambda name: object())
    window.missing.cache_clear()
    try:
        assert window.missing() is None
        monkeypatch.setattr(window.importlib.util, "find_spec", lambda name: None)
        window.missing.cache_clear()
        assert "PyObjC" in window.missing()
    finally:
        window.missing.cache_clear()


def test_mac_window_uses_its_python_and_private_environment(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(window, "IS_WINDOWS", False)
    monkeypatch.setattr(window, "IS_MACOS", True)
    monkeypatch.setattr(window, "missing", functools.cache(lambda: None))
    monkeypatch.setenv("DECOMP_WINDOW", "1")
    monkeypatch.setattr(window.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(window.subprocess, "Popen", popen)
    assert window.Window(tmp_path, "http://127.0.0.1:6474/", "private-token", lambda: None).open()
    argv, kwargs = calls[0]
    assert argv[:3] == [sys.executable, "-I", str(window.MAC_WINDOW_PROGRAM)]
    assert "private-token" not in " ".join(argv)
    assert kwargs["env"]["DECOMP_WINDOW_TOKEN"] == "private-token"


@pytest.mark.skipif(os.name == "nt", reason="the installer is a POSIX shell script")
def test_mac_installer_skips_linux_packages_and_tracks_this_fork(tmp_path):
    bindir = tmp_path / "test bin"
    bindir.mkdir()
    calls = tmp_path / "calls"
    scripts = {
        "uname": 'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac',
        "uv": 'case "$*" in --version) echo "uv 0.13.5";; "tool dir --bin") echo ' + shlex.quote(str(bindir)) + ';; esac',
        "curl": 'echo \'{"sha":"' + "a" * 40 + '"}\'',
        "decomp": 'printf "%s\\n" "$*" >> ' + shlex.quote(str(calls)),
    }
    for name, text in scripts.items():
        path = bindir / name
        path.write_text("#!/bin/sh\n" + text + "\n")
        path.chmod(0o755)
    script = tmp_path / "install.sh"
    script.write_bytes((Path(__file__).resolve().parents[1] / "site/public/install.sh").read_bytes())
    env = dict(os.environ, HOME=str(tmp_path / "home"), PATH=str(bindir) + ":/usr/bin:/bin")
    result = subprocess.run(["bash", str(script), "--no-start", "--source", "branch:main"],
                            capture_output=True, text=True, env=env, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text()
    assert "--repo RetroReversing/decompdecomp" in recorded and "--branch main" in recorded
    assert "setup" in recorded and "shortcut" in recorded
    assert "GTK" not in result.stdout and "sudo" not in result.stdout


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("DECOMP_TEST_COCOA") != "1",
                    reason="set DECOMP_TEST_COCOA=1 on a Mac desktop for the native WebKit smoke test")
def test_native_webkit_loads_the_app_and_obeys_close_confirmation():
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("native_macwindow.py"))],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "native macOS window passed" in result.stdout


@pytest.mark.parametrize("kind,accepted", [("folder", True), ("file", True), ("file", False)])
def test_native_picker_selects_files_or_folders_and_honors_cancel(monkeypatch, capsys, kind, accepted):
    settings = {}
    panel = SimpleNamespace(
        setTitle_=lambda value: settings.update(title=value),
        setCanChooseDirectories_=lambda value: settings.update(directories=value),
        setCanChooseFiles_=lambda value: settings.update(files=value),
        setAllowsMultipleSelection_=lambda value: settings.update(multiple=value),
        runModal=lambda: 1 if accepted else 0,
        URL=lambda: SimpleNamespace(path=lambda: "/Users/owner/My save"))
    app = SimpleNamespace(setActivationPolicy_=lambda _: None, activateIgnoringOtherApps_=lambda _: None)
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(
        NSApplication=SimpleNamespace(sharedApplication=lambda: app), NSModalResponseOK=1,
        NSOpenPanel=SimpleNamespace(openPanel=lambda: panel)))
    monkeypatch.setattr(picker.sys, "platform", "darwin")
    picker.main(kind, "Open a save")
    assert capsys.readouterr().out == ("/Users/owner/My save\n" if accepted else "")
    assert settings == {"title": "Open a save", "directories": kind == "folder", "files": kind == "file", "multiple": False}


def test_finder_can_find_homebrew_uv_without_a_shell_profile(monkeypatch):
    monkeypatch.setattr(hostenv.sys, "platform", "darwin")
    monkeypatch.setattr(hostenv.shutil, "which", lambda _: None)
    monkeypatch.setattr(hostenv.Path, "is_file", lambda path: path.as_posix() == "/opt/homebrew/bin/uv")
    assert Path(hostenv.find_uv()).as_posix() == "/opt/homebrew/bin/uv"


def test_demo_prerequisites_distinguish_path_tools_from_unavailable_compilers(monkeypatch, tmp_path):
    from . import test_consoles

    monkeypatch.setattr(test_consoles, "ZLIB", tmp_path)
    monkeypatch.setattr(toolchains, "installed", lambda tid: {} if tid == "gcc-2.7.2-kmc" else None)
    monkeypatch.setattr(shutil, "which",
                        lambda name: "/tools/" + name)
    demo = SimpleNamespace(demo_tools=("gcc-2.7.2-kmc", "ld.lld", "llvm-objcopy"))
    assert test_consoles._missing(demo) == []
    demo.demo_tools = ("ee-gcc-2.96",)
    assert test_consoles._missing(demo) == ["ee-gcc-2.96"]


@pytest.mark.parametrize("tid", ["gcc-2.7.2-psx", "gcc-2.7.2-cdk-psx", "gcc-2.8.1-psx"])
def test_ps1_mac_archives_are_native_arm64_only(tid, monkeypatch):
    """The published PS1 Mac archives are arm64, so never offer them to Intel Macs or Linux hosts."""
    monkeypatch.setattr(toolchains.wsl, "wanted", lambda: False)
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-arm64")
    tc = toolchains.get(tid)
    assert tc.supported and not tc.proprietary and toolchains.runtime_error(tid) is None
    parts = tc.parts_for()
    assert len(parts) == 1 and parts[0]["platforms"] == ["macos-arm64"]
    assert parts[0]["url"].endswith("-macos.tar.gz")
    assert len(parts[0]["sha256"]) == 64 and not parts[0].get("rosetta")
    for dep in tc.requires:
        assert toolchains.get(dep).supported
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-x86_64")
    assert not tc.supported
    assert toolchains.get("maspsx").supported  # its Python source works on either Mac architecture
    assert tc.parts_for("linux-x86_64")[0]["url"].endswith(("-psx.tar.gz", "-cdk.tar.gz"))


@pytest.mark.parametrize("cpu", ["arm64", "x86_64"])
def test_early_ps1_compiler_builds_natively_on_macs(cpu, monkeypatch):
    monkeypatch.setattr(toolchains.wsl, "wanted", lambda: False)
    monkeypatch.setattr(toolchains, "current_platform", lambda: "macos-" + cpu)
    tc = toolchains.get("gcc-2.6.3-psx")
    assert tc.supported and not tc.proprietary
    parts = tc.parts_for()
    assert len(parts) == 1 and parts[0]["build"] == "psx-gcc-2.6.3"
    assert all(len(parts[0][key]) == 64 for key in ("sha256", "patches_sha256", "config_guess_sha256", "config_sub_sha256"))
    assert tc.parts_for("linux-x86_64")[0]["url"].endswith("gcc-2.6.3-psx.tar.gz")


@pytest.mark.parametrize("host,expected", [("darwin", "/Applications/Compiler App/python3.12"), ("linux", "python3")])
def test_ps1_assembler_uses_app_python_on_mac_only(host, expected, tmp_path, monkeypatch):
    from decompnet import project

    monkeypatch.setattr(project.sys, "platform", host)
    monkeypatch.setattr(project.sys, "executable", "/Applications/Compiler App/python3.12")
    monkeypatch.setattr(toolchains.Toolchain, "via_wsl", property(lambda _: False))
    monkeypatch.setattr(toolchains, "runtime_dirs", lambda _: (tmp_path / "gcc", tmp_path / "mas", tmp_path / "bin"))
    cfg = project.compiler_config(tmp_path, {"compiler": {"toolchain": "gcc-2.7.2-psx"}})
    assert cfg.cmd[10] == expected
    assert '"$py" -E -s "$mas/maspsx.py"' in cfg.cmd[2]
    assert "{python}" not in " ".join(cfg.cmd)


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS PS1 compilers")
@pytest.mark.parametrize("tid", ["gcc-2.6.0-psx", "gcc-2.6.3-psx", "gcc-2.7.2-psx", "gcc-2.7.2-cdk", "gcc-2.8.0-psx", "gcc-2.8.1-psx"])
def test_native_ps1_compiler_produces_little_endian_mips(tid, tmp_path):
    from io import BytesIO

    from elftools.elf.elffile import ELFFile

    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config

    if any(toolchains.installed(t) is None for t in (tid, "maspsx", "binutils-mips")):
        pytest.skip("needs the pinned PS1 compiler, maspsx and MIPS binutils")
    cfg = compiler_config(tmp_path, {"compiler": {"toolchain": tid}})
    result = compile_source(cfg, "", "int add(int a, int b) { return a+b; }\nint divide(int a, int b) { return a/b; }")
    assert result.ok, result.log
    elf = ELFFile(BytesIO(result.obj))
    assert elf["e_machine"] == "EM_MIPS" and elf.little_endian
    assert elf.get_section_by_name(".text").data()


@pytest.mark.parametrize("version", ["2.6.0", "2.8.0"])
def test_more_ps1_source_builds_are_pinned_for_mac(version):
    tc = toolchains.get("gcc-" + version + "-psx")
    for cpu in ("arm64", "x86_64"):
        part, = tc.parts_for("macos-" + cpu)
        assert part["build"] == "psx-gcc" and part["version"] == version
        assert all(len(part[key]) == 64 for key in ("sha256", "patches_sha256", "config_guess_sha256", "config_sub_sha256"))
    assert not toolchains.get("gcc-2.5.7-psx").parts_for("macos-arm64")
    assert not toolchains.get("gcc-2.91.66-psx").parts_for("macos-arm64")
    assert not toolchains.get("gcc-2.95.2-psx").parts_for("macos-arm64")


def test_macos_bundle_check_rejects_checkout_virtual_environment(tmp_path):
    # A venv copied to Runtime can import the app yet still depend on the developer's Python.
    # Exercise the build script's actual check against such a runtime, without downloads/building.
    script = (Path(__file__).resolve().parents[1]/'packaging/macos/build.sh').read_text()
    check = next(line for line in script.splitlines() if 'sys.prefix.endswith' in line)
    command = shlex.split(check)
    runtime = tmp_path/'Runtime'
    subprocess.run([sys.executable,'-m','venv','--without-pip',str(runtime)],check=True)
    python = runtime/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    result = subprocess.run([str(python),'-c',command[-1]],capture_output=True,text=True)
    assert result.returncode != 0 and 'AssertionError' in result.stderr


@pytest.mark.skipif(os.name=='nt' or not shutil.which('bash'),reason='POSIX packaging shell required')
def test_macos_build_temporary_files_respect_selected_volume(tmp_path):
    script=(Path(__file__).resolve().parents[1]/'packaging/macos/build.sh').read_text()
    prefix=script.split('uv python install',1)[0]
    result=subprocess.run(['bash','-c',prefix+'\nprintf "%s" "$WORK"'],
                          env={**os.environ,'TMPDIR':str(tmp_path)},capture_output=True,text=True,check=True)
    created=Path(result.stdout)
    assert created.parent==tmp_path and not created.exists()


# What the 32-bit Linux releases (decompals/old-gcc 0.17, under qemu-i386) print for the PS1 float check, all three
# versions alike, and what the native builds printed before their CONST_DOUBLE and real.h fixes.
_PSX_FLOATS_GOOD = ('\tli.d\t$6,2.50000000000000000000e+00\n'
                    '\t.word\t0x8800759c\t\t# 1.0000000000000000525e+300\n\t.word\t0x7e37e43c\n'
                    '\t.word\t0x7f61b1e6\t\t# 3.0000000055e+38 (float)\n'
                    '\t.word\t0x8800759c\t\t# -1.0000000000000000525e+300\n\t.word\t0xfe37e43c\n'
                    '\t.word\t0xff61b1e6\t\t# -3.0000000055e+38 (float)\n'
                    '\t.word\t0x00000000\t\t# -nan\n\t.word\t0xfff80000\n')


def _psx_check(tmp_path, monkeypatch, listing, warning_exit=0, error_exit=33):
    def run(argv, **kwargs):
        src = Path(argv[argv.index('-o') - 1]).name
        if src == 'psx_floats.c':
            Path(argv[argv.index('-o') + 1]).write_text(listing)
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        if src == 'psx_warning.c':
            return SimpleNamespace(returncode=warning_exit, stdout='', stderr='warning: initialization makes pointer')
        return SimpleNamespace(returncode=error_exit, stdout='', stderr="`undeclared' undeclared")
    monkeypatch.setattr(toolchains.subprocess, 'run', run)
    toolchains._check_psx_gcc(tmp_path / 'cc1', tmp_path, '2.6.3')


def test_native_ps1_gcc_check_accepts_the_32_bit_release_output(tmp_path, monkeypatch):
    _psx_check(tmp_path, monkeypatch, _PSX_FLOATS_GOOD)


@pytest.mark.parametrize('listing', [
    # 2.8.0 before the fix: each double's high word from CONST_DOUBLE_LOW's never-written half, floats 0.
    _PSX_FLOATS_GOOD.replace('0x7e37e43c', '0x00000000').replace('0xfe37e43c', '0x00000000')
                    .replace('0x7f61b1e6', '0x00000000').replace('0xff61b1e6', '0x00000000'),
    # 2.6.x with only the CONST_DOUBLE copies fixed: real.h read each word through `long *`.
    _PSX_FLOATS_GOOD.replace('0x8800759c\t\t# 1.0', '0x7e37e43c8800759c\t\t# 1.0')
                    .replace('\t.word\t0x7e37e43c\n', '\t.word\t0x7e37e43c8800759c\n'),
    # A word sign-extended into a 64-bit long prints sixteen digits.
    _PSX_FLOATS_GOOD.replace('0xff61b1e6', '0xffffffffff61b1e6'),
    # 0.0 / 0.0 folded with arm64's default NaN (sign clear), not x86's.
    _PSX_FLOATS_GOOD.replace('0xfff80000', '0x7ff80000'),
    # A constant `li.d` should load went to the pool.
    _PSX_FLOATS_GOOD.replace('\tli.d\t$6,2.50000000000000000000e+00\n', '\t.word\t0x00000000\n\t.word\t0x40040000\n'),
])
def test_native_ps1_gcc_check_refuses_the_broken_builds_seen(tmp_path, monkeypatch, listing):
    with pytest.raises(toolchains.ToolchainError, match='folds float constants wrongly'):
        _psx_check(tmp_path, monkeypatch, listing)


@pytest.mark.parametrize('warning_exit, error_exit', [(-10, 33), (0, -11)])  # bus error on a warning, segfault
def test_native_ps1_gcc_check_refuses_builds_that_crash_on_diagnostics(tmp_path, monkeypatch, warning_exit, error_exit):
    with pytest.raises(toolchains.ToolchainError, match="doesn't report diagnostics"):
        _psx_check(tmp_path, monkeypatch, _PSX_FLOATS_GOOD, warning_exit, error_exit)


def _psx_source(root, version):
    """The texts the PS1 GCC edits replace, as the pinned sources have them."""
    files = {}
    for name, edits in toolchains._psx_const_double_edits(version).items():
        files.setdefault(name, []).extend(old for old, _ in edits)
    for name, lines in toolchains._psx_declarations(version).items():
        files.setdefault(name, []).extend(lines)
    for name, texts in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text('\n'.join(texts) + '\n', encoding='latin-1')


@pytest.mark.parametrize('version', ['2.6.0', '2.6.3', '2.8.0'])
def test_native_ps1_gcc_edits_copy_const_doubles_word_by_word(tmp_path, version):
    _psx_source(tmp_path, version)
    toolchains._edit_exactly(tmp_path, toolchains._psx_declarations(version),
                             toolchains._psx_const_double_edits(version), 'test')
    assert 'static void decomp_cd_get' in (tmp_path / 'rtl.h').read_text()
    assert 'extern void   error ();' not in (tmp_path / 'config/mips/mips.c').read_text()
    if version.startswith('2.6.'):
        assert 'DECOMP_X86_NAN (value, d1, d2);' in (tmp_path / 'fold-const.c').read_text()
        assert 'DECOMP_X86_NAN (value, f0, f1);' in (tmp_path / 'cse.c').read_text()
        assert 'error_with_decl (thunk,' in (tmp_path / 'cp/method.c').read_text()
    else:
        assert '- INTVAL (const_arg1) >= 0' not in (tmp_path / 'cse.c').read_text()
    assert 'CONST_DOUBLE_LOW' not in (tmp_path / 'varasm.c').read_text()
    real = (tmp_path / 'real.h').read_text()
    assert 'CONST_DOUBLE_LOW' not in real
    assert ('(long *)' in real) is False and (('(unsigned int *)' in real) == version.startswith('2.6.'))


def test_native_ps1_gcc_build_fails_closed_when_the_source_moves(tmp_path):
    _psx_source(tmp_path, '2.6.0')
    (tmp_path / 'varasm.c').write_text('/* moved */\n')
    with pytest.raises(toolchains.ToolchainError, match='no longer matches'):
        toolchains._edit_exactly(tmp_path, toolchains._psx_declarations('2.6.0'),
                                 toolchains._psx_const_double_edits('2.6.0'), 'GCC 2.6.0')


@pytest.mark.parametrize('tid', ['gcc-2.6.0-psx', 'gcc-2.6.3-psx', 'gcc-2.8.0-psx'])
def test_native_ps1_gcc_installs_built_before_the_float_fix_are_rebuilt(tid):
    """The recipe number is in the parts' fingerprint, so earlier Mac builds (both architectures) are out of date."""
    for plat in ('macos-arm64', 'macos-x86_64'):
        part, = toolchains.get(tid).parts_for(plat)
        assert part['recipe'] == 2
        assert 'mirrors.slackware.com' not in part['url']


@pytest.mark.skipif(sys.platform != 'darwin', reason='native macOS PS1 compilers')
@pytest.mark.parametrize('tid', ['gcc-2.6.0-psx', 'gcc-2.6.3-psx', 'gcc-2.8.0-psx'])
def test_installed_native_ps1_gcc_passes_the_float_check(tid, tmp_path):
    if toolchains.installed(tid) is None or toolchains.changed(tid, toolchains.installed(tid)):
        pytest.skip('needs the current native PS1 compiler')
    toolchains._check_psx_gcc(toolchains.path(tid) / 'cc1', tmp_path, tid)


def _x87_fix(tmp_path):
    """Compile the header's x87 conversions with the host compiler and print a few out-of-range conversions."""
    if not shutil.which('cc'):
        pytest.skip('needs a C compiler')
    src = tmp_path / 'fix.c'
    src.write_text(toolchains._PSX_26_X87_FIX + '#include <stdio.h>\nint main(void) {\n'
                   '  double v[] = {-1.0, 3e9, -3e9, 5e9, 1e20, 0.0 / 0.0, 2147483647.9, -2147483648.9};\n'
                   '  for (int i = 0; i < 8; i++) printf("%d %u\\n", REAL_VALUE_FIX (v[i]), REAL_VALUE_UNSIGNED_FIX (v[i]));\n'
                   '  return 0;\n}\n')
    subprocess.run(['cc', '-O0', '-w', str(src), '-o', str(tmp_path / 'fix')], check=True)
    return subprocess.run([str(tmp_path / 'fix')], capture_output=True, text=True, check=True).stdout.split('\n')[:-1]


def test_ps1_gcc_26_converts_folded_floats_as_the_32_bit_release_does(tmp_path):
    """x87: out-of-range or NaN to int is 0x80000000; to unsigned, the low word of a 64-bit conversion."""
    assert _x87_fix(tmp_path) == ['-1 4294967295', '-2147483648 3000000000', '-2147483648 1294967296',
                                  '-2147483648 705032704', '-2147483648 0', '-2147483648 0',
                                  '2147483647 2147483647', '-2147483648 2147483648']
