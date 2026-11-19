"""Pre-flight: a project is checked to build before helpers spend compiles on it. A project folder copied from
/home/olduser to /home/sam still ran /home/olduser/.local/share/uv/tools/decompnet/bin/python in its build.ninja,
and every compile failed with "/bin/sh: 1: ...: not found" (five compiles, two sessions that then quit)."""

import json
import sys
import types
from pathlib import Path

import pytest

from decompnet.app import agents as agents_mod
from decompnet.app.engine import RomNetwork
from decompnet.forks.project import ForkProject

GONE = "/home/olduser/.local/share/uv/tools/decompnet/bin/python"


def _project(tmp_path, fixes: bool):
    root = tmp_path / "ws" / "p"
    (root / "tools").mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    (root / ".decomp.wtf.json").write_text(json.dumps({"platform": "pc", "setup": [["python", "tools/configure.py"]]}))
    (root / "build.ninja").write_text(f"PY = {GONE}\ncflags = /O2 /G6 /DNDEBUG\nrule cc\n  command = $PY tools/x.py\n")
    py = "sys.executable" if fixes else repr(GONE)
    (root / "tools" / "configure.py").write_text(
        "import sys\nopen('build.ninja', 'w').write('PY = ' + " + py + " + '\\nrule cc\\n  command = $PY x\\n')\n")
    return root


def test_build_problem_finds_programs_build_ninja_runs_that_arent_here(tmp_path):
    root = _project(tmp_path, fixes=True)
    fp = ForkProject(root)
    why = fp.build_problem()
    assert why is not None and GONE in why and "/O2" not in why  # MSVC flags aren't paths
    tool = (root / "tools" / "configure.py").as_posix()  # a program that's here, by absolute path on any system
    (root / "build.ninja").write_text(f"PY = {sys.executable}\nrule cc\n  command = {tool} x\n")
    assert fp.build_problem() is None
    (root / "objdiff.json").unlink()
    assert "objdiff.json" in fp.build_problem()


def test_build_problem_keeps_a_path_with_spaces_whole(tmp_path):
    """#320: macOS keeps this app's toolchains in ~/Library/Application Support, and the first word of a
    command ended at the space, so a project that built fine looked broken (quoted and $-escaped alike)."""
    tool = tmp_path / "My Tools" / "cc"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\n")
    gone = "/home/olduser/No Such Tool/cc"  # not here on any system, and /-rooted: the check reads only those
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    fp = ForkProject(root)
    escaped = tool.as_posix().replace(" ", "$ ")
    for ninja in (f'rule cc\n  command = "{tool}" -c $in\n',  # shell-quoted: the DS template's style
                  f"rule cc\n  command = {escaped} -c $in\n",  # ninja's own escaping of a space
                  f'PY = "{tool}"\nrule cc\n  command = $PY -c $in\n'):  # a top-level variable too
        (root / "build.ninja").write_text(ninja)
        assert fp.build_problem() is None, ninja
    (root / "build.ninja").write_text(f'rule cc\n  command = "{gone}" -c $in\n')
    why = fp.build_problem()
    assert why is not None and gone in why  # the whole path, not its start at the space


def _ninja_syntax():
    """The ninja_syntax.py the DS template writes build.ninja with (its tools/, not an import path)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ninja_syntax", Path(__file__).parents[1] / "src/decompnet/consoles/nds/project_template/tools/ninja_syntax.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wrapped_project(tmp_path, program: str) -> ForkProject:
    root = tmp_path / "ws" / "p"
    root.mkdir(parents=True, exist_ok=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    command = f'"{program}" rom extract --rom $in --output-path extract/aekx'
    # the width a long home gives at 78 columns: the last space before it is the one in "Application Support"
    # (tmp_path's own length would move it)
    width = len("  command = ") + command.index(" ") + len(" $") + 1
    with open(root / "build.ninja", "w", encoding="utf-8") as f:
        _ninja_syntax().Writer(f, width=width).rule("extract", command)
    assert "Application $\n" in (root / "build.ninja").read_text()  # it really was wrapped inside the quotes
    return ForkProject(root)


def test_build_problem_names_a_missing_ninja_syntax_wrapped_path_whole(tmp_path):
    """#320 as the DS template writes it: ninja_syntax wraps a command past 78 columns at its last space before
    that, which in a long "~/Library/Application Support/..." is the one inside the quotes, so the line ended at
    "Application $" and the path went on, indented, on the next line."""
    gone = "/home/olduser/Library/Application Support/decompnet/toolchains/dsd/dsd"
    why = _wrapped_project(tmp_path, gone).build_problem()
    assert why is not None and gone in why  # the whole path, joined back from both lines


@pytest.mark.skipif(sys.platform == "win32", reason="the check reads only /-rooted paths, and Windows' are C:\\...")
def test_build_problem_finds_a_ninja_syntax_wrapped_path_thats_here(tmp_path):
    """#320: the same wrapped line, the program there, is no problem (it paused agents before every session)."""
    tool = tmp_path / "Users" / "francescanaclerio" / "Library" / "Application Support" / "decompnet" / "dsd" / "dsd"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\n")
    assert _wrapped_project(tmp_path, str(tool)).build_problem() is None


def test_preflight_and_helpers_start_on_a_toolchain_path_with_a_space(tmp_path):
    """#320, the whole road at once: a macOS DS project's configure.py writes build.ninja running
    a tool from ~/Library/Application Support (quoted and $-escaped), and pre-flight — which had
    just reconfigured the project, another computer's paths in its build.ninja — must let a
    helper start a session, not pause it on a path cut off at the space."""
    tool = tmp_path / "Library" / "Application Support" / "decompnet" / "toolchains" / "mwccarm" / "mwccarm"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\n")
    root = tmp_path / "ws" / "p"
    (root / "tools").mkdir(parents=True)
    (root / "objdiff.json").write_text(json.dumps({"units": []}))
    (root / ".decomp.wtf.json").write_text(json.dumps({"platform": "pc", "setup": [["python", "tools/configure.py"]]}))
    (root / "build.ninja").write_text(f"PY = {GONE}\nrule cc\n  command = $PY -c $in\n")  # another computer's
    escaped = tool.as_posix().replace(" ", "$ ")
    ninja = f'rule cc\n  command = "{tool}" -c $in\nrule link\n  command = {escaped} $in\n'
    (root / "tools" / "configure.py").write_text("open('build.ninja', 'w').write(" + repr(ninja) + ")\n")
    net = RomNetwork(tmp_path / "d.db", tmp_path / "ws")
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "ready": True, "platform": "pc"}, "x")
    assert net.forks.preflight("p") is None  # reconfigured, and its spaced paths check out
    assert str(tool) in (root / "build.ninja").read_text()  # by running configure, not by skipping the check
    fake = types.SimpleNamespace(net=net)
    assert agents_mod.Manager._build_problem(fake, "p") is None  # so a helper starts a session


def test_preflight_runs_the_setup_again_and_keeps_what_it_couldnt_fix(tmp_path):
    for fixes in (True, False):
        net = RomNetwork(tmp_path / f"d{fixes}.db", tmp_path / "ws")
        root = _project(tmp_path / str(fixes), fixes)
        net.forks._register("p", "P", "fork", root, {"kind": "fork", "ready": True, "platform": "pc"}, "x")
        why = net.forks.preflight("p")
        if fixes:
            assert why is None and sys.executable in (root / "build.ninja").read_text()
        else:
            assert why is not None and GONE in why
            assert net.forks.overview("p")["build_problem"] == why  # the project's page says so


def test_helpers_dont_start_a_session_on_a_project_that_doesnt_build(tmp_path):
    net = RomNetwork(tmp_path / "d.db", tmp_path / "ws")
    root = _project(tmp_path, fixes=False)
    net.forks._register("p", "P", "fork", root, {"kind": "fork", "ready": True, "platform": "pc"}, "x")
    fake = types.SimpleNamespace(net=net)
    assert GONE in agents_mod.Manager._build_problem(fake, "p")
    (root / "build.ninja").write_text(f"PY = {sys.executable}\n")
    assert agents_mod.Manager._build_problem(fake, "p") is None
