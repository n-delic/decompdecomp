"""#81: Watcom reaches Setup and the real PC engine without a proprietary compiler pack.

Real-tool coverage opts in to the homemade PE linked by projects/demo-watcom's sources, never a bundled game.
CI exercises the kit and selection with synthetic compiler bodies and skips the real compile/import checks.
"""

import hashlib
import os
from pathlib import Path

import pytest

from decompnet import consoles, kits, toolchains
from decompnet.consoles.pc import scratch
from decompnet.kits import watcom


def make_copy(root):
    for rel in ("BINNT/WCC386.EXE", "BINNT/WPP386.EXE", "H/STDDEF.H"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"loader")
    (root / "BINB").mkdir()
    for name, data in (("WCC386.EXE", b"c"), ("WPP386.EXE", b"cpp")):
        (root / "BINB" / name).write_bytes(data * watcom.MIN_BODY_BYTES)
    return root


def test_setup_lists_separate_languages_and_uses_the_bodies_builds(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "WATCOM")
    digest = hashlib.sha256((root / "BINB/WCC386.EXE").read_bytes()).hexdigest()
    monkeypatch.setitem(watcom.KNOWN, digest, "10.0a")
    kit = kits.get("watcom")
    assert kit.locate(tmp_path) == root.resolve()
    copies = kit.compilers(root)
    assert len(copies) == 2 and all(c.family == "Watcom" for c in copies)
    assert copies[0].build == "10.0a" and copies[1].build == "unknown"
    monkeypatch.setattr(toolchains, "local_folders", lambda kid: [root] if kid == "watcom" else [])
    pc = consoles.get("pc")
    shown = [c for c in pc.describe()["compilers"] if c["id"].startswith("Watcom/")]
    assert len(shown) == 2 and all(c["local"] and c["target"] == "x86" for c in shown)
    monkeypatch.setattr(toolchains, "installed", lambda *_: None)
    monkeypatch.setattr(toolchains, "ensure", lambda *_args, **_kw: None)
    monkeypatch.setattr(toolchains, "binary", lambda *_: tmp_path / "fake-wibo")
    args = pc.tool_args()
    assert "--msvc" not in args and "--watcom" in args and "--own" in args


def test_incomplete_body_is_not_offered_as_a_working_compiler(tmp_path):
    root = make_copy(tmp_path)
    (root / "BINB/WCC386.EXE").write_bytes(bytes(65536))
    assert [c.exe.name for c in kits.get("watcom").compilers(root)] == ["WPP386.EXE"]


@pytest.mark.parametrize("dos", [False, True])
def test_setting_watcom_as_the_default_keeps_its_namespace_and_changes_family_flags(tmp_path, dos):
    import json

    (tmp_path / "config/v").mkdir(parents=True)
    (tmp_path / "config/v/functions.json").write_text(json.dumps({"format": "LE" if dos else "PE"}))
    (tmp_path / "tools").mkdir()
    cfg = tmp_path / "tools/configure.py"
    cfg.write_text('MSVC_VERSION = "6.0"\nCL_FLAGS = ["/c", "/O2"]\nLTCG = True\n')
    scratch.set_own_compiler(tmp_path, {"version": "v", "compiler": "MSVC/6.0"}, "Watcom/watcom-copy-c-123", ["src/a.c"])
    assert 'MSVC_VERSION = "watcom-copy-c-123"' in cfg.read_text()
    assert '"-3r"' in cfg.read_text() and '"/O2"' not in cfg.read_text()
    assert 'LTCG = False' in cfg.read_text()
    assert json.loads((tmp_path / ".decomp.wtf.json").read_text())["compiler"] == "Watcom/watcom-copy-c-123"
    assert ("-bt=dos4g" if dos else "-bt=nt") in cfg.read_text()
    assert scratch.compiler_id("watcom-copy-c-123") == "Watcom/watcom-copy-c-123"


def test_switching_one_source_to_watcom_clears_msvc_flags(tmp_path):
    (tmp_path / 'config/v').mkdir(parents=True)
    scratch.set_file_flags(tmp_path, 'v', 'src/a.c', ['/Oy-'])
    scratch.set_file_compiler(tmp_path, 'v', 'src/a.c', 'Watcom/watcom-copy-c-123', 'MSVC/6.0')
    assert scratch.file_flags(tmp_path, 'v').get('src/a.c') is None


@pytest.mark.parametrize("language", ["c", "cpp"])
def test_real_watcom_project_import_attempts_polishing_and_rebuild(tmp_path, monkeypatch, language):
    own, game = os.environ.get("DECOMP_TEST_WATCOM_C"), os.environ.get("DECOMP_TEST_WATCOM_PE")
    if not own or not game:
        pytest.skip("needs the local Watcom C copy and its homemade PE: DECOMP_TEST_WATCOM_C / DECOMP_TEST_WATCOM_PE")
    if not all(toolchains.installed(t) for t in ("ghidra", "jdk", "wibo", "objdiff-cli")):
        pytest.skip("needs pinned Ghidra, JDK, wibo and objdiff")
    monkeypatch.setenv("HOME", str(tmp_path))
    original = toolchains.local_folders
    monkeypatch.setattr(toolchains, "local_folders", lambda kid: [Path(own)] if kid == "watcom" else original(kid))
    from decompnet.app.engine import RomNetwork

    net = RomNetwork(tmp_path / "app.db", tmp_path)
    copies = kits.compilers(("watcom",))
    compiler = next(c for c in copies if f"-{language}-" in c.version)
    slug = net.import_rom(Path(game), confirmed=True, toolchain=f"Watcom/{compiler.version}", log=lambda *_: None)
    overview = net.overview(slug)
    assert overview["ready"] and overview["rebuild"]["identical"]
    assert overview["compiler"]["toolchain"] == f"Watcom/{compiler.version}"
    root = Path(net.project_row(slug)["root"])
    assert list((root / "src").rglob("*.c" if language == "c" else "*.cpp"))
    agent, _ = net.local_agent("watcom-test")
    assert len(net.forks.autopilot_prepare(slug)) == 2
    first, second = "_func_00410000", "_func_00410003"
    assert "00410000" in net.claim(agent, slug, first)
    assert "Watcom" in net.get_function(first, slug)
    bad = net.attempt(agent, first, "int func_00410000(int a,int b) {return a-b;}", slug)
    assert not bad["matched"]
    good = '#include "ghidra.h"\nint func_00410000(int a,int b) {return a+b;}'
    cpp = next(c for c in copies if c.version != compiler.version)
    tried = net.forks.try_compiler(agent, slug, first, f"Watcom/{cpp.version}", source="int func_00410000(int a,int b) {return a-b;}")
    assert cpp.version in tried
    assert "match" in net.forks.search_flags(agent, slug, first, source=good).lower()
    assert net.attempt(agent, first, good, slug)["matched"]
    start = "extern int D_00420000; void func_00410003(void) {D_00420000=func_00410000(2,3);}"
    assert net.attempt(agent, second, start, slug)["matched"]
    assert scratch.check_code(root, "c", log=lambda *_: None)["identical"]
    assert "readable" in net.readability.submit(agent, slug, first, "/* Add two integers. */\n" + good)
    assert net.rename_symbol(agent, slug, first, "add_values").startswith("✅")
    assert scratch.check_code(root, "c", log=lambda *_: None)["identical"]
    net.forks.check(slug, log=lambda *_: None)
    assert net.overview(slug)["rebuild"]["identical"]


def test_splitting_a_source_keeps_its_calling_convention_flags(tmp_path):
    import json

    folder = tmp_path / "config/v"
    folder.mkdir(parents=True)
    (tmp_path / "src/auto").mkdir(parents=True)
    (tmp_path / "src/auto/unit.c").write_text("")
    first, second = "_func_00401000", "_func_00401010"
    (folder / "functions.json").write_text(json.dumps({"units": {"unit": [{"name": first}, {"name": second}]}}))
    (folder / "file_flags.json").write_text(json.dumps({"src/auto/unit.c": ["-3s"]}))
    changed = scratch.split_unit(tmp_path, "v", "src/auto/unit.c", second, "src/auto/next.c")
    assert folder / "file_flags.json" in changed
    assert scratch.file_flags(tmp_path, "v")["src/auto/next.c"] == ["-3s"]


@pytest.mark.parametrize("dos", [False, True])
def test_per_file_watcom_choice_uses_the_executable_target(tmp_path, dos):
    import json
    import shutil
    import subprocess
    import sys

    shutil.copytree(Path(scratch.__file__).with_name("project_template") / "tools", tmp_path / "tools")
    (tmp_path / "src/auto").mkdir(parents=True)
    (tmp_path / "src/auto/one.c").write_text("int f(void) { return 1; }\n")
    config = tmp_path / "config/v/functions.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"exe": "orig/game.exe", "format": "LE" if dos else "PE", "units": {"one": []}}))
    config.with_name("file_compilers.json").write_text(json.dumps({"src/auto/one.c": "watcom-test"}))
    subprocess.run([sys.executable, str(tmp_path / "tools/configure.py"), "v",
                    "--own", "watcom-test=none,unused.exe", "--watcom", "watcom-test=unused"],
                   cwd=tmp_path, check=True, capture_output=True, timeout=30)
    ninja = (tmp_path / "build.ninja").read_text()
    assert ("-bt=dos4g" if dos else "-bt=nt") in ninja
    assert "baseflags = /nologo" not in ninja
    assert "tools/segments.py" in ninja.split("build all_source:")[0]
