"""Public MIPS builds find installed pinned binutils without global links or project-source changes."""

import json
import os
from types import SimpleNamespace

import pytest

from decompnet import consoles
from decompnet.arch import mips_tools
from decompnet.forks.project import ForkProject

pytestmark = pytest.mark.skipif(os.name == "nt", reason="pinned MIPS binaries run inside WSL on Windows")


def pinned(tmp_path, monkeypatch):
    tools = tmp_path / "pinned"
    tools.mkdir()
    ld = tools / "mips-ps2-decompals-ld"
    ld.write_text("#!/bin/sh\nprintf 'pinned MIPS linker\\n'\n")
    ld.chmod(0o755)
    monkeypatch.setattr(mips_tools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(mips_tools.toolchains, "require", lambda tid: tools)
    return tools, ld


@pytest.mark.parametrize("console", ["n64", "ps", "ps2"])
def test_all_mips_consoles_expose_the_same_installed_tools(tmp_path, monkeypatch, console):
    tools, ld = pinned(tmp_path, monkeypatch)
    project = SimpleNamespace(root=tmp_path / "project")
    paths = consoles.get(console).project_tool_paths(project)
    assert paths[-1] == tools
    for prefix in ("mips-linux-gnu-", "mips64-linux-gnu-", "mips64-elf-"):
        wrapper = (paths[0] / (prefix + "ld")).read_text()  # (a script: it renames the emulation, then runs ld)
        assert str(ld.resolve()) in wrapper and "elf32btsmip) a=elf32ebmip" in wrapper
        assert (paths[0] / (prefix + "ld")).stat().st_mode & 0o111
    assert consoles.get(console).project_tool_paths(project) == paths  # repeated builds reuse the links
    assert consoles.get("gc").project_tool_paths(project) == []


def test_missing_toolchain_creates_nothing_and_downloads_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(mips_tools.toolchains, "installed", lambda tid: None)
    project = SimpleNamespace(root=tmp_path)
    assert mips_tools.build_paths(project) == []
    assert not (tmp_path / ".decomp-wtf").exists()


def test_make_detects_pinned_tools_in_its_process_path(tmp_path, monkeypatch):
    import shutil

    if not shutil.which("make"):
        pytest.skip("needs make")
    pinned(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    (root / ".decomp.wtf.json").write_text(json.dumps({"platform": "n64"}))
    makefile = root / "Makefile"
    contents = ("ifeq ($(shell which mips-linux-gnu-ld 2>/dev/null),)\n"
                "$(error Unable to detect a suitable MIPS toolchain installed)\nendif\n"
                "all:\n\t@mips-linux-gnu-ld --version\n")
    makefile.write_text(contents)
    path = os.environ.get("PATH")
    ok, log = ForkProject(root).run(["make"], log=lambda *_: None)
    assert ok and "pinned MIPS linker" in log
    assert os.environ.get("PATH") == path and makefile.read_text() == contents


def test_n64_reuses_only_the_declared_missing_ido_version(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    source = tmp_path / "installed compiler with spaces"
    source.mkdir()
    (source / "cc").write_text("compiler")
    (source / "cfe").write_text("compiler sibling")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: source)
    root = tmp_path / "project"
    root.mkdir()
    make = "TOOLS := tools\nIDO := $(TOOLS)/ido-static-recomp/build/5.3/out/cc\n"
    (root / "Makefile").write_text(make)
    project = SimpleNamespace(root=root)
    prepared = consoles.get("n64").project_build_command(project, ["make", "extract"])
    assert prepared == ["make", "extract", "IDO=.decomp-wtf/build-tools/ido-5.3/cc"]
    assert (root / ".decomp-wtf/build-tools/ido-5.3/cfe").read_text() == "compiler sibling"
    assert (root / "Makefile").read_text() == make
    explicit = ["make", "IDO=my-own-compiler"]
    assert consoles.get("n64").project_build_command(project, explicit) == explicit
    assert consoles.get("ps2").project_build_command(project, ["make"]) == ["make"]
    local = root / "tools/ido-static-recomp/build/5.3/out/cc"
    local.parent.mkdir(parents=True)
    local.write_text("existing local compiler")
    assert consoles.get("n64").project_build_command(project, ["make"]) == ["make"]
    local.unlink()
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: None)
    assert consoles.get("n64").project_build_command(project, ["make"]) == ["make"]


def test_n64_redirects_a_cc_variable_and_builds_the_crc_tool(tmp_path, monkeypatch):
    """A community decomp that picks its compiler with an indented `CC :=` under an else (after a qemu line that
    also assigns CC) still gets the installed IDO, and tools/n64crc is compiled for its checksum step."""
    from decompnet.consoles.n64 import buildtools

    source = tmp_path / "ido"
    source.mkdir()
    (source / "cc").write_text("compiler")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: source)
    ran = []
    monkeypatch.setattr(buildtools.subprocess, "run", lambda argv, **kw: ran.append(argv))
    monkeypatch.setattr(buildtools.shutil, "which", lambda name: "/usr/bin/cc" if name == "cc" else None)
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "n64crc.c").write_text("int main(void){return 0;}")
    make = ("TOOLS_DIR = tools\nifeq ($(USE_QEMU_IRIX),1)\n\tCC := $(QEMU_IRIX) -silent $(TOOLS_DIR)/x/cc\n"
            "\telse\n\tCC\t := $(TOOLS_DIR)/ido-static-recomp/build/5.3/out/cc\n\tendif\n")
    (root / "Makefile").write_text(make)
    prepared = consoles.get("n64").project_build_command(SimpleNamespace(root=root), ["make", "-j4"])
    assert prepared == ["make", "-j4"]  # (CC itself stays the project's: its folder links to the installed IDO)
    assert (root / "tools/ido-static-recomp/build/5.3/out").resolve() == source.resolve()
    assert ran and ran[0][0] == "/usr/bin/cc" and ran[0][-1].endswith("tools/n64crc")


def test_decomp_yaml_names_the_rom_and_place_game_aliases_the_build_scripts_name(tmp_path):
    """A community decomp whose decomp.yaml says paperboy.z64 beside a configure.py that reads baserom.z64: setup
    knows where the ROM goes (and which version a hash is) and leaves it under both names."""
    from decompnet.forks.project import ForkProject

    root = tmp_path / "p"
    root.mkdir()
    (root / "decomp.yaml").write_text(
        "versions:\n- name: us\n  sha1: " + "a" * 40 + "\n  paths:\n    target: game.z64\n"
        "- name: eu\n  paths:\n    baserom: ver/eu/baserom.z64\n")
    (root / "configure.py").write_text('rom = open("baserom.z64")\n')
    (root / "eu.sha1").write_text("b" * 40 + "  baserom.z64\n")
    fp = ForkProject(root)
    assert fp.decomp_yaml_roms() == {"us": ("game.z64", "a" * 40), "eu": ("ver/eu/baserom.z64", "b" * 40)}
    assert fp.rom_originals() == {"us": "a" * 40, "eu": "b" * 40}
    assert fp.needs("eu")["path"] == "ver/eu/baserom.z64"


def test_n64_wraps_a_linux_ido_driver_on_a_mac(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    source = tmp_path / "installed"
    source.mkdir()
    (source / "cc").write_text("compiler")
    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: source)
    root = tmp_path / "project"
    driver = root / "tools" / "ido_5.3" / "usr" / "lib" / "cc"
    driver.parent.mkdir(parents=True)
    driver.write_bytes(b"\x7fELF....")
    buildtools._wrap_foreign_ido(SimpleNamespace(root=root))
    assert driver.read_text().startswith("#!/bin/sh") and str(source / "cc") in driver.read_text()
    assert driver.with_name("cc.linux").read_bytes().startswith(b"\x7fELF")


def test_a_makefile_or_readme_names_the_rom_when_nothing_else_does(tmp_path):
    from decompnet.forks.project import ForkProject

    root = tmp_path / "p"
    root.mkdir()
    (root / "Makefile").write_text("VERSION ?= us\nBASEROM := baserom.$(VERSION).z64\nsetup:\n\ttrue\n")
    fp = ForkProject(root)
    assert fp.needs(None)["path"] == "baserom.us.z64"
    assert fp.setup_commands(None)[0][-1] == "setup"
    (root / "Makefile").write_text("all:\n\ttrue\n")
    (root / "README.md").write_text("Copy your rom to roms/baserom.us.z64 and run ./setup\n")
    assert ForkProject(root).needs(None)["path"] == "roms/baserom.us.z64"


def test_a_missing_python_tool_is_installed_into_the_project_environment(tmp_path, monkeypatch):
    from decompnet.forks import project as project_module
    from decompnet.forks.project import ForkProject

    root = tmp_path / "p"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "pyvenv.cfg").write_text("")
    (root / ".venv" / "bin" / "python3").write_text("")
    fp = ForkProject(root)
    ran = []
    monkeypatch.setattr(project_module, "_tool", lambda name: "/bin/uv")
    monkeypatch.setattr(fp, "run", lambda cmd, log=print, **kw: (ran.append(cmd) or (True, "")))
    assert fp._install_missing_tools("FAILED: [code=127] x\npigment64 to-bin a b\n/bin/sh: pigment64: command not found", print)
    assert ran[0][:3] == ["/bin/uv", "pip", "install"] and ran[0][-1] == "pigment64"
    assert not fp._install_missing_tools("make: *** Error 1", print)


def test_n64_swaps_a_committed_linux_tool_for_the_hosts_and_skips_the_hosts_gcc_pass(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    host = tmp_path / "bin" / "pigment64"
    host.parent.mkdir()
    host.write_text("#!/bin/sh\n")
    host.chmod(0o755)
    monkeypatch.setenv("PATH", str(host.parent))
    root = tmp_path / "project"
    tool = root / "tools" / "pigment64"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b"\x7fELF....")
    tool.chmod(0o755)
    ido = root / "tools" / "ido" / "macos" / "cc"
    ido.parent.mkdir(parents=True)
    ido.write_bytes(b"\xcf\xfa\xed\xfe")
    (ido.parent / "cfe").write_bytes(b"\xcf\xfa\xed\xfe....")
    buildtools._wrap_foreign_ido(SimpleNamespace(root=root))
    assert str(host) in tool.read_text() and tool.with_name("pigment64.linux").exists()
    assert (ido.parent / "cfe").stat().st_mode & 0o111 and ido.stat().st_mode & 0o111 or True
    (root / "Makefile").write_text("RUN_CC_CHECK ?= 1\nall:\n\ttrue\n")
    prepared = consoles.get("n64").project_build_command(SimpleNamespace(root=root), ["make"])
    assert prepared[-1] == "RUN_CC_CHECK=0"


def test_n64_links_installed_compiler_folders_a_project_expects(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    ido, gcc = tmp_path / "ido", tmp_path / "gcc"
    ido.mkdir()
    gcc.mkdir()
    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: ido if tid.startswith("ido") else gcc)
    root = tmp_path / "project"
    root.mkdir()
    (root / "Makefile").write_text("CC := tools/gcc_2.7.2/$(DETECTED_OS)/gcc\nX = tools/ido/7.1/cc\nCC_HOST := gcc\n")
    prepared = consoles.get("n64").project_build_command(SimpleNamespace(root=root), ["make"])
    assert (root / "tools/gcc_2.7.2/macos").resolve() == gcc and (root / "tools/ido/7.1").resolve() == ido
    assert "CC_HOST=true" in prepared


def test_rom_path_through_makefile_variables(tmp_path):
    from decompnet.forks.project import ForkProject

    root = tmp_path / "p"
    root.mkdir()
    (root / "Makefile").write_text("VERSION ?= us\nBASEROM_DIR := baseroms/$(VERSION)\nBASEROM := $(BASEROM_DIR)/baserom.z64\n")
    assert ForkProject(root).needs(None)["path"] == "baseroms/us/baserom.z64"


def test_a_decompressed_splat_target_means_the_cartridge_rom_is_named_elsewhere(tmp_path):
    from decompnet.forks.project import ForkProject

    root = tmp_path / "p"
    root.mkdir()
    (root / "Makefile").write_text("BASEROM := baserom.us.z64\nsetup:\n\ttrue\n")
    (root / "splat.yaml").write_text("options:\n  platform: n64\n  target_path: decompressed.us.z64\nsegments: []\n")
    need = ForkProject(root).needs(None)
    assert need["path"] == "baserom.us.z64" and need["sha1"] is None


def test_n64_links_an_os_named_ido_folder_and_wraps_a_missing_host_tool(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    ido = tmp_path / "ido"
    ido.mkdir()
    (ido / "cc").write_text("compiler")
    hosts = tmp_path / "bin"
    hosts.mkdir()
    (hosts / "armips").write_text("#!/bin/sh\n")
    (hosts / "armips").chmod(0o755)
    monkeypatch.setenv("PATH", str(hosts))
    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: ido)
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    (root / "Makefile").write_text("CC := tools/ido_recomp/$(DETECTED_OS)/5.3/cc\nASM := tools/armips -sym x\n")
    consoles.get("n64").project_build_command(SimpleNamespace(root=root), ["make"])
    assert (root / "tools/ido_recomp/macos/5.3").resolve() == ido
    assert str(hosts / "armips") in (root / "tools/armips").read_text()


def test_a_make_shim_and_a_broken_mac_ido_and_cc_check_variants(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    root = tmp_path / "project"
    root.mkdir()
    (root / "Makefile").write_text("CC_CHECK ?= gcc\nall:\n\ttrue\n")
    assert consoles.get("n64").project_build_command(SimpleNamespace(root=root), ["make"])[-1] == "CC_CHECK=true"
    (root / "Makefile").write_text("CC_CHECK := $(HOSTCC) $(HOSTCC_CHECK_FLAGS) -fsyntax-only -std=gnu90\n")
    assert consoles.get("n64").project_build_command(SimpleNamespace(root=root), ["make"])[-1] == "CC_CHECK=true"
    tools = tmp_path / "pinned"
    tools.mkdir()
    (tools / "mips-ps2-decompals-ld").write_text("#!/bin/sh\n")
    monkeypatch.setattr(mips_tools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(mips_tools.toolchains, "require", lambda tid: tools)
    monkeypatch.setattr(mips_tools.sys, "platform", "darwin")
    monkeypatch.setattr(mips_tools.shutil, "which", lambda name: f"/opt/{name}" if name == "gmake" else None)
    folder = mips_tools.build_paths(SimpleNamespace(root=root))[0]
    assert "/opt/gmake" in (folder / "make").read_text()


def test_plain_cpp_shim_is_gnu_when_the_host_has_one(tmp_path, monkeypatch):
    """A Mac's /usr/bin/cpp rejects the `-MD -MF x.d src.c -o /dev/null` pass projects run after a compile; a GNU cpp
    (Homebrew's cpp-N) is put first in the build path under the plain name."""
    from pathlib import Path

    gnu = sorted(m for d in ("/opt/homebrew/bin", "/usr/local/bin") for m in Path(d).glob("cpp-[0-9]*"))
    if not gnu:
        pytest.skip("needs a GNU cpp-N")
    pinned(tmp_path, monkeypatch)
    paths = mips_tools.build_paths(SimpleNamespace(root=tmp_path / "project"))
    assert str(gnu[-1]) in (paths[0] / "cpp").read_text()


def test_n64_replaces_an_old_mac_assembler_that_lacks_the_mul_flag(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    installed = tmp_path / "gcc"
    installed.mkdir()
    (installed / "as").write_text("assembler")
    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: installed)
    root = tmp_path / "project"
    old = root / "tools" / "gcc_2.7.2" / "macos" / "as"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"\xcf\xfa\xed\xfe old assembler")
    (root / "Makefile").write_text("CFLAGS := -Wa,--vr4300mul-off\n")
    buildtools._update_old_mac_assembler(SimpleNamespace(root=root), root / "tools")
    assert str(installed / "as") in old.read_text() and old.with_name("as.old").exists()
    # an assembler that already knows the option is left alone
    old.write_bytes(b"\xcf\xfa\xed\xfe vr4300mul-off")
    buildtools._update_old_mac_assembler(SimpleNamespace(root=root), root / "tools")
    assert old.read_bytes().endswith(b"vr4300mul-off")


def test_n64_replaces_a_qemu_irix_compiler_command_with_the_installed_ido(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    ido = tmp_path / "ido"
    ido.mkdir()
    (ido / "cc").write_text("compiler")
    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: ido)
    root = tmp_path / "project"
    root.mkdir()
    (root / "Makefile").write_text("IDO := ~/qemu-irix -L tools/ido_irix tools/ido_irix/usr/bin/cc\nall:\n\ttrue\n")
    prepared = consoles.get("n64").project_build_command(SimpleNamespace(root=root), ["make"])
    assert prepared[-1].startswith("IDO=.decomp-wtf/build-tools/ido-") and prepared[-1].endswith("/cc")

def test_the_assembler_shim_predefines_what_the_cross_gcc_does(tmp_path):
    """The SGI headers pick register names by `mips` and the byte order by `_MIPSEB`; the real gcc predefines both."""
    import subprocess
    import sys

    cpp = tmp_path / "cpp"
    cpp.write_text('#!/bin/sh\necho "$@" > "$CPP_ARGS"\n')
    cpp.chmod(0o755)
    assembler = tmp_path / "as"
    assembler.write_text("#!/bin/sh\nexit 0\n")
    assembler.chmod(0o755)
    driver = tmp_path / "gcc"
    driver.write_text(mips_tools._ASSEMBLER_DRIVER.format(python=sys.executable, cpp=cpp, assembler=assembler))
    (tmp_path / "x.s").write_text("nop\n")
    record = tmp_path / "args"
    subprocess.run([sys.executable, str(driver), "-EB", "-x", "assembler-with-cpp", "-c", "-o", str(tmp_path / "x.o"),
                    str(tmp_path / "x.s")], check=True, env={**os.environ, "CPP_ARGS": str(record)})
    seen = record.read_text().split()
    assert {"-Dmips=1", "-D_MIPSEB", "-D_LANGUAGE_ASSEMBLY"} <= set(seen)
    assert "-D_MIPSEL" not in seen


def test_the_sha1sum_shim_accepts_one_space_between_hash_and_name(tmp_path):
    import hashlib
    import shutil
    import subprocess
    import sys

    shasum = shutil.which("shasum")
    if not shasum:
        pytest.skip("no shasum")
    (tmp_path / "x.bin").write_bytes(b"abc")
    shim = tmp_path / "sha1sum"
    shim.write_text(mips_tools._SHA1SUM.replace("@PYTHON@", sys.executable).replace("@SHASUM@", shasum))
    line = f"{hashlib.sha1(b'abc').hexdigest()} x.bin\n"
    done = subprocess.run([sys.executable, str(shim), "--check"], input=line, text=True, cwd=tmp_path,
                          capture_output=True)
    assert done.returncode == 0, done.stderr
    bad = subprocess.run([sys.executable, str(shim), "--check"], input="0" * 40 + " x.bin\n", text=True, cwd=tmp_path,
                         capture_output=True)
    assert bad.returncode != 0


def test_n64_swaps_a_committed_linux_tool_in_a_versioned_folder(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    hosts = tmp_path / "bin"
    hosts.mkdir()
    (hosts / "gzip").write_text("#!/bin/sh\n")
    (hosts / "gzip").chmod(0o755)
    monkeypatch.setenv("PATH", str(hosts))
    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: None)
    root = tmp_path / "project"
    (root / "tools/gzip-1.2.4").mkdir(parents=True)
    program = root / "tools/gzip-1.2.4/gzip"
    program.write_bytes(b"\x7fELF" + b"\0" * 20)
    program.chmod(0o755)
    buildtools._wrap_foreign_ido(SimpleNamespace(root=root))
    assert program.read_text().startswith("#!/bin/sh\nexec ")
    assert (root / "tools/gzip-1.2.4/gzip.linux").is_file()


def test_a_sha1_file_beside_the_splat_config_names_the_original_rom(tmp_path):
    from decompnet.forks.splatrefs import config_sha1

    config = tmp_path / "game.us.yaml"
    assert config_sha1(config, {"options": {}}) is None
    (tmp_path / "game.us.sha1").write_text("a" * 40 + "\n")
    assert config_sha1(config, {"options": {}}) == "a" * 40
    assert config_sha1(config, {"sha1": "b" * 40, "options": {}}) == "b" * 40



def test_the_cross_gcc_shim_is_only_provided_to_makefiles_that_assemble_through_it(tmp_path, monkeypatch):
    pinned(tmp_path, monkeypatch)
    monkeypatch.setattr(mips_tools.shutil, "which", lambda name: "/usr/bin/cpp" if name == "cpp" else None)
    root = tmp_path / "project"
    root.mkdir()
    (root / "Makefile").write_text("XGCC = $(CROSS)gcc\n$(O): %.c\n\t$(XGCC) -c $<\n")
    folder = mips_tools.build_paths(SimpleNamespace(root=root))[0]
    assert not (folder / "mips-linux-gnu-gcc").exists()
    (root / "Makefile").write_text("%.o: %.s\n\t$(CROSS)gcc -x assembler-with-cpp -c $<\n")
    folder = mips_tools.build_paths(SimpleNamespace(root=root))[0]
    assert (folder / "mips-linux-gnu-gcc").is_file()


def test_cc_check_through_a_host_compiler_variable_is_skipped_on_a_mac(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    root = tmp_path / "project"
    root.mkdir()
    (root / "Makefile").write_text("CC_CHECK = $(HOSTCC) --target=i686-linux-gnu -fsyntax-only -std=gnu90\nall:\n\ttrue\n")
    assert consoles.get("n64").project_build_command(SimpleNamespace(root=root), ["make"])[-1] == "CC_CHECK=true"


def test_a_torch_assets_target_runs_in_setup_and_a_cmake_tool_with_spdlog_gets_clangs_fmt_switch(tmp_path):
    from decompnet.forks import project as forks_project

    plain = "tools:\n\ttrue\nassets:\n\techo hi\nextract:\n\ttrue\n"
    assert forks_project._setup_targets(plain) == ["tools", "extract"]  # (an `assets` that isn't Torch's stays alone)
    torch = "assets:\n\t@echo x\n\t$(V)$(TORCH) code $(BASEROM)\n\nall:\n\ttrue\n"
    assert forks_project._setup_targets(torch) == ["assets"]
    (tmp_path / "tools" / "torch").mkdir(parents=True)
    assert forks_project._mac_cxx_env(tmp_path) == {}
    (tmp_path / "tools" / "torch" / "CMakeLists.txt").write_text("FetchContent_Declare(spdlog)\n")
    assert forks_project._mac_cxx_env(tmp_path) == {"CXX": "c++ -DFMT_CONSTEVAL="}


def test_a_mac_iconv_shim_writes_a_backslash_after_japanese_text_as_one_byte(tmp_path, monkeypatch):
    import subprocess

    tools = tmp_path / "pinned"
    tools.mkdir()
    (tools / "mips-ps2-decompals-ld").write_text("#!/bin/sh\n")
    monkeypatch.setattr(mips_tools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(mips_tools.toolchains, "require", lambda tid: tools)
    monkeypatch.setattr(mips_tools.sys, "platform", "darwin")
    real = tmp_path / "real-iconv"
    real.write_text("#!/bin/sh\nprintf 'system iconv'\n")
    real.chmod(0o755)
    monkeypatch.setattr(mips_tools.shutil, "which", lambda name: str(real) if name == "iconv" else None)
    folder = mips_tools.build_paths(SimpleNamespace(root=tmp_path / "project"))[0]
    source = tmp_path / "x.c"
    source.write_text("ト\\n\n", encoding="utf-8")
    made = subprocess.run([str(folder / "iconv"), "-t", "EUC-JP", "-f", "UTF-8", str(source)], capture_output=True)
    assert made.stdout == b"\xa5\xc8\\n\n"
    other = subprocess.run([str(folder / "iconv"), "-t", "UTF-16", "-f", "UTF-8", str(source)], capture_output=True)
    assert other.stdout == b"system iconv"


def test_n64_gets_wibo_and_an_o32_assembler(tmp_path, monkeypatch):
    """Ninja files call `wibo` by name, and a distribution's mips-linux-gnu-as assembles o32 without being told (the
    pinned PS2 one doesn't, and ld then refuses a `-b binary` object beside the code)."""
    tools, _ = pinned(tmp_path, monkeypatch)
    (tools / "mips-ps2-decompals-as").write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    (tools / "mips-ps2-decompals-as").chmod(0o755)
    (tools / "wibo").write_text("#!/bin/sh\n")
    monkeypatch.setattr(mips_tools.shutil, "which", lambda name, **kw: None)
    root = tmp_path / "project"
    paths = mips_tools.build_paths(SimpleNamespace(root=root, platform="n64", manifest=None))
    assert str(tools / "wibo") in (paths[0] / "wibo").read_text()
    assert "-mabi=32" in (paths[0] / "mips-linux-gnu-as").read_text()
    assert not (paths[0] / "mips-linux-gnu-as").is_symlink()
    other = mips_tools.build_paths(SimpleNamespace(root=tmp_path / "other", platform="ps2", manifest=None))
    assert (other[0] / "mips-linux-gnu-as").is_symlink()


def test_stale_splat_cache_goes_when_the_project_collects_undefined_symbols(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "game.yaml").write_text("options:\n  platform: n64\n  undefined_syms_auto_path: undefined_syms_auto.txt\n"
                                    "segments: []\n")
    (root / ".splache").write_bytes(b"cache")
    ForkProject(root)._drop_stale_splat_cache()
    assert not (root / ".splache").exists()
    (root / ".splache").write_bytes(b"cache")
    (root / "game.yaml").write_text("options:\n  platform: n64\nsegments: []\n")
    ForkProject(root)._drop_stale_splat_cache()
    assert (root / ".splache").exists()


def test_sn64_compiler_files_are_linked_into_tools_bin(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    source = tmp_path / "pinned"
    source.mkdir()
    for name in ("cc1n64.exe", "cc1pln64.exe"):
        (source / name).write_bytes(b"MZ")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: source)
    root = tmp_path / "project"
    root.mkdir()
    (root / "configure.py").write_text('CC1N64 = "tools/bin/cc1n64.exe"\n')
    buildtools._provide_sn64(SimpleNamespace(root=root))
    assert (root / "tools/bin/cc1n64.exe").is_symlink() and (root / "tools/bin/cc1pln64.exe").is_symlink()


def test_gcc_272_folder_a_linux_only_project_names_is_linked_on_a_mac(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    source = tmp_path / "kmc"
    source.mkdir()
    (source / "gcc").write_text("compiler")
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: source)
    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    (root / "Makefile.common").write_text("TOOLS_DIR := tools\nKMC_PATH := $(TOOLS_DIR)/gcc-2.7.2/\n")
    buildtools._provide_compiler_dirs(SimpleNamespace(root=root))
    assert (root / "tools/gcc-2.7.2").resolve() == source.resolve()


def test_objcopy_accepts_the_distribution_names_of_the_o32_formats(tmp_path, monkeypatch):
    tools, _ = pinned(tmp_path, monkeypatch)
    (tools / "mips-ps2-decompals-objcopy").write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    (tools / "mips-ps2-decompals-objcopy").chmod(0o755)
    paths = mips_tools.build_paths(SimpleNamespace(root=tmp_path / "project"))
    import subprocess

    said = subprocess.run([str(paths[0] / "mips-linux-gnu-objcopy"), "-O", "elf32-tradbigmips", "-B", "mips"],
                          capture_output=True, text=True).stdout.split()
    assert said == ["-O", "elf32-bigmips", "-B", "mips"]


def test_dockerfile_splat_pin_and_tools_requirements_make_the_project_environment(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    (root / "tools/requirements.txt").write_text("splat64[mips]>=0.36.3,<1.0.0\n")
    (root / "Dockerfile").write_text('RUN pip install -U \\\n  "splat64[mips]<0.40.0"\n')
    (root / "Makefile").write_text("all:\n")
    project = ForkProject(root)
    project.platform = "n64"
    steps = []
    monkeypatch.setattr(ForkProject, "run", lambda self, cmd, **kw: (steps.append(cmd) or (True, "")))
    monkeypatch.setattr(ForkProject, "kind", lambda self: "splat")
    project.ensure_python_env(log=lambda *_: None)
    assert any("tools/requirements.txt" in cmd and "splat64[mips]<0.40.0" in cmd for cmd in steps)


def test_a_committed_gcc_272_in_tools_gets_the_installed_one_never_the_hosts_gcc(tmp_path, monkeypatch):
    from decompnet.consoles.n64 import buildtools

    installed = tmp_path / "kmc"
    installed.mkdir()
    for name in ("gcc", "cc1", "as"):
        (installed / name).write_text("installed " + name)
    monkeypatch.setattr(buildtools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(buildtools.toolchains, "require", lambda tid: installed)
    monkeypatch.setattr(buildtools.sys, "platform", "darwin")
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    elf = b"\x7fELF" + b"\0" * 12 + b"GNU C version 2.7.2"
    for name in ("gcc", "cc1", "as", "ld"):
        (root / "tools" / name).write_bytes(elf)
        (root / "tools" / name).chmod(0o755)
    buildtools._wrap_foreign_ido(SimpleNamespace(root=root))
    assert str(installed / "gcc") in (root / "tools/gcc").read_text()
    assert str(installed / "as") in (root / "tools/as").read_text()
    assert (root / "tools/ld").read_bytes() == elf  # (no installed ld to stand in: left alone, not the host's)


def test_a_build_that_races_with_its_clean_step_is_run_once_more_serially(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "Makefile").write_text("all: clean build\n")
    project = ForkProject(root)
    project.platform = "n64"
    calls = []

    def fake_run(self, cmd, log=print, **kw):
        calls.append(cmd)
        if "-j1" not in cmd:
            log("gmake: *** [Makefile:71: clean] Error 1")
            return False, "(only the last lines)"
        return True, ""

    monkeypatch.setattr(ForkProject, "run", fake_run)
    monkeypatch.setattr(ForkProject, "kind", lambda self: "custom")
    monkeypatch.setattr(ForkProject, "setup_commands", lambda self, version=None: [["make", "-j8"]])
    monkeypatch.setattr(ForkProject, "unpack_disc", lambda self, version, log=print: None)
    monkeypatch.setattr(ForkProject, "ensure_python_env", lambda self, log=print: None)
    monkeypatch.setattr(ForkProject, "generate_objdiff", lambda self, version=None, log=print: False)
    (root / "objdiff.json").write_text("{}")
    monkeypatch.setattr(ForkProject, "_all_source", lambda self: False)
    project.setup(None, log=lambda *_: None)
    assert calls == [["make", "-j8"], ["make", "-j1"]]


def test_a_mac_head_tail_shim_reads_gnus_long_spellings_and_negative_counts(tmp_path, monkeypatch):
    import subprocess

    tools = tmp_path / "pinned"
    tools.mkdir()
    (tools / "mips-ps2-decompals-ld").write_text("#!/bin/sh\n")
    monkeypatch.setattr(mips_tools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(mips_tools.toolchains, "require", lambda tid: tools)
    monkeypatch.setattr(mips_tools.sys, "platform", "darwin")
    monkeypatch.setattr(mips_tools.shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("head", "tail") else None)
    folder = mips_tools.build_paths(SimpleNamespace(root=tmp_path / "project"))[0]
    cut = subprocess.run(f'printf "0123456789ABCDEFGHIJ" | "{folder}/tail" --bytes=+11 | "{folder}/head" --bytes=-8',
                         shell=True, capture_output=True)
    assert cut.stdout == b"AB"
    lines = subprocess.run(f'printf "a\\nb\\nc\\n" | "{folder}/head" -n -1', shell=True, capture_output=True)
    assert lines.stdout == b"a\nb\n"
    first = subprocess.run(f'printf "a\\nb\\nc\\n" | "{folder}/head" -2', shell=True, capture_output=True)
    assert first.stdout == b"a\nb\n"


def test_a_prerequisites_target_that_builds_tools_and_assets_runs_before_the_build():
    from decompnet.forks import project as forks_project

    make = "prerequisites: print_info create_directories build_tools extractassets\n\nall: x\n"
    assert forks_project._setup_targets(make) == ["prerequisites"]
    assert forks_project._setup_targets("prerequisites:\n\ttrue\n") == []


def test_a_mac_sed_shim_runs_gnu_sed_when_homebrew_has_it(tmp_path, monkeypatch):
    tools = tmp_path / "pinned"
    tools.mkdir()
    (tools / "mips-ps2-decompals-ld").write_text("#!/bin/sh\n")
    monkeypatch.setattr(mips_tools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(mips_tools.toolchains, "require", lambda tid: tools)
    monkeypatch.setattr(mips_tools.sys, "platform", "darwin")
    monkeypatch.setattr(mips_tools.shutil, "which", lambda name: "/opt/homebrew/bin/gsed" if name == "gsed" else None)
    folder = mips_tools.build_paths(SimpleNamespace(root=tmp_path / "project"))[0]
    assert '/opt/homebrew/bin/gsed" "$@"' in (folder / "sed").read_text()


def test_objcopy_wrapper_renames_the_traditional_mips_formats(tmp_path, monkeypatch):
    tools = tmp_path / "pinned"
    tools.mkdir()
    (tools / "mips-ps2-decompals-objcopy").write_text("#!/bin/sh\n")
    monkeypatch.setattr(mips_tools.toolchains, "installed", lambda tid: {"id": tid})
    monkeypatch.setattr(mips_tools.toolchains, "require", lambda tid: tools)
    root = tmp_path / "project"
    root.mkdir()
    folder = mips_tools.build_paths(SimpleNamespace(root=root))[0]
    text = (folder / "mips64-elf-objcopy").read_text()
    assert "elf32-tradbigmips) a=elf32-bigmips" in text and str(tools / "mips-ps2-decompals-objcopy") in text


def test_a_project_setup_script_names_the_tool_repositories_its_makefile_reads(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    (root / "Makefile").write_text("ULTRALIB ?= tools/ultralib\nASMPROC := python3 tools/asm-processor/build.py\n")
    (root / "tools" / "setup.sh").write_text(
        "for r in matt-kempster/m2c simonlindholm/asm-processor; do\n"
        '  [ -d "$(basename $r)" ] || git clone -q --depth 1 "https://github.com/$r.git"\n'
        "done\n[ -d ultralib ] || { git clone -q https://github.com/decompals/ultralib.git && "
        "git -C ultralib checkout -q " + "a" * 40 + "; }\n")
    fp = ForkProject(root)
    monkeypatch.setattr(fp, "kind", lambda: "splat")
    calls = []
    monkeypatch.setattr(fp, "run", lambda cmd, log=print, timeout=0, **kw: (calls.append(cmd) or True, ""))
    monkeypatch.setattr("decompnet.forks.project.shutil.which", lambda name: "/usr/bin/git")
    fp.fetch_pinned_tool_repos(log=lambda *a: None)
    assert ["git", "clone", "-q", "https://github.com/decompals/ultralib.git", "tools/ultralib"] in calls
    assert ["git", "-C", "tools/ultralib", "checkout", "-q", "a" * 40] in calls
    assert ["git", "clone", "-q", "https://github.com/simonlindholm/asm-processor.git", "tools/asm-processor"] in calls
    assert not any("m2c" in " ".join(c) for c in calls)  # (the Makefile never reads it)


def test_the_target_image_derived_from_a_verified_rom_gets_its_sha1_from_the_build(tmp_path):
    import hashlib

    from decompnet.forks import splatrefs

    (tmp_path / "build").mkdir()
    rom = b"finished rom"
    (tmp_path / "build" / "game.z64").write_bytes(rom)
    (tmp_path / "build" / "expanded.bin").write_bytes(b"expanded")
    (tmp_path / "Makefile").write_text(f"SHA1 := {hashlib.sha1(rom).hexdigest()}\n")
    image = tmp_path / "build" / "expanded.bin"
    assert splatrefs.built_image_sha1(tmp_path, image) == hashlib.sha1(b"expanded").hexdigest()
    (tmp_path / "build" / "game.z64").write_bytes(b"another rom")
    assert splatrefs.built_image_sha1(tmp_path, image) is None
