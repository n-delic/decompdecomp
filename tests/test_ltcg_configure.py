"""configure.py in an LTCG project: a unit whose driver (made from the skeleton) defines HarnessEntry gets its
harness link, a HARNESS-LINK line that can't be read stops configure with where it is and leaves the last
build.ninja whole, a unit's code split over files links in its order, and objects depend on what they include."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from decompnet.consoles.pc import scratch as scratch_pe
from decompnet.forks import ltcg

TEMPLATE = scratch_pe.TEMPLATE
# the skeleton's comment as projects made before this fix have it in every driver
OLD_LINE = '// - `// HARNESS-LINK: <flags>` on a line here adds linker flags (d3d8ltcg.lib, /d2:"-inlT56"...).\n'


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    shutil.copytree(TEMPLATE, root)
    ltcg.prepare(root, {"u": [SimpleNamespace(addr=0x11000)]})
    cfg = root / "tools" / "configure.py"
    cfg.write_text(cfg.read_text().replace('MSVC_VERSION = "6.0"', 'MSVC_VERSION = "7.1"'))  # as an Xbox project
    for version in ("7.1", "6.0"):  # (configure names the compilers; nothing compiles here)
        (root / "msvc" / version / "Bin").mkdir(parents=True)
        (root / "msvc" / version / "Bin" / "CL.EXE").write_bytes(b"")
    (root / "config" / "v").mkdir(parents=True)
    (root / "config" / "v" / "functions.json").write_text(json.dumps({"exe": "orig/v.xbe", "sha1": "0", "units": {
        "u": [{"name": "_func_00011000", "addr": 0x11000, "offset": 0, "size": 1, "relocs": []}]}}))
    (root / "src" / "auto").mkdir(parents=True)
    (root / "src" / "auto" / "u.cpp").write_text("// FUNCTION: 0x11000\nvoid func_00011000() {}\n")
    (root / "include").mkdir()
    return root


def _configure(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "tools/configure.py", "v", "--msvc", str(root / "msvc")], cwd=root,
                          capture_output=True, text=True)


def _ninja(root: Path) -> str:
    """build.ninja with its continued lines joined."""
    return re.sub(r" *\$\n\s*", " ", (root / "build.ninja").read_text())


def _entry(root: Path, extra: str = "") -> None:
    drv = root / "harness" / "u.drv.cpp"
    text = drv.read_text().replace('// extern "C" __declspec(dllexport) void HarnessEntry(void)\n// {\n// }\n',
                                   'extern "C" __declspec(dllexport) void HarnessEntry(void)\n{\n}\n')
    drv.write_text(extra + text)


def test_a_skeleton_driver_with_harness_entry_gets_the_harness_link(tmp_path):
    root = _project(tmp_path)
    assert "HARNESS-LINK:" not in ltcg.DRIVER  # the skeleton doesn't spell the directive out
    _entry(root, OLD_LINE)  # an old skeleton's comment only mentions it: not a directive (it has an open quote)
    res = _configure(root)
    assert res.returncode == 0, res.stderr
    ninja = _ninja(root)
    assert "build build/v/ltcg/u/u.dll | build/v/ltcg/u/u.map build/v/ltcg/u/u.map.link.txt: ltcg_link" in ninja
    assert "build build/v/src/auto/u.obj | build/v/src/auto/u.ltcg.json: ltcg_coff" in ninja
    assert not (root / "build.ninja.tmp").exists() and json.loads((root / "objdiff.json").read_text())["units"]


def test_an_unreadable_harness_link_line_says_where_and_keeps_the_last_build_file(tmp_path):
    root = _project(tmp_path)
    assert _configure(root).returncode == 0
    before = (root / "build.ninja").read_text()
    _entry(root, '// HARNESS-LINK: /d2:"-inlT56\n')
    res = _configure(root)
    assert res.returncode != 0
    assert "u.drv.cpp:1: can't read the HARNESS-LINK flags" in res.stderr and "Traceback" not in res.stderr
    assert (root / "build.ninja").read_text() == before


def test_a_units_code_split_over_files_links_in_its_link_order_and_only_there(tmp_path):
    root = _project(tmp_path)
    _entry(root)
    auto = root / "src" / "auto"
    (auto / "u.scores.cpp").write_text("int g;\n")
    (auto / "u.main.cpp").write_text("void m() {}\n")
    assert _configure(root).returncode == 0
    ninja = _ninja(root)
    # no LINK-ORDER line: the unit's source first, then its parts by name, then the driver
    for obj, src in (("00_u", "u.cpp"), ("01_u_main", "u.main.cpp"), ("02_u_scores", "u.scores.cpp")):
        assert f"build build/v/ltcg/u/{obj}.obj: ltcg_cc src/auto/{src}" in ninja
    assert "build build/v/ltcg/u/03_u_drv.obj: ltcg_cc harness/u.drv.cpp" in ninja
    assert "u.main.obj:" not in ninja and "u.scores.obj:" not in ninja  # never compiled on their own
    # a new file in src/auto or harness (a part, a stand-in) makes ninja run configure again: the folders are inputs
    # of configure.facts, which build.ninja depends on (and which a new file changes)
    facts = re.search(r"^build build/v/configure\.facts: facts (.*) \|", ninja, re.M)
    assert facts and {"src/auto", "harness"} <= set(facts.group(1).split())
    assert "build src/auto: phony" in ninja  # (a folder or file that's gone makes the facts out of date)
    assert re.search(r"^build build\.ninja: configure .*build/v/configure\.facts", ninja, re.M)
    body = (auto / "u.cpp").read_text()
    (auto / "u.cpp").write_text("// LINK-ORDER: u.scores.cpp src/auto/u.cpp\n" + body)
    assert _configure(root).returncode == 0
    ninja = _ninja(root)
    for obj, src in (("00_u_scores", "u.scores.cpp"), ("01_u", "u.cpp"), ("02_u_main", "u.main.cpp")):
        assert f"build build/v/ltcg/u/{obj}.obj: ltcg_cc src/auto/{src}" in ninja
    # a name that isn't one of the unit's files stops configure with where, and the last build.ninja stays
    before = (root / "build.ninja").read_text()
    (auto / "u.cpp").write_text("// LINK-ORDER: u.score.cpp u.cpp\n" + body)
    res = _configure(root)
    assert res.returncode != 0 and "Traceback" not in res.stderr
    assert "u.cpp:1: LINK-ORDER names u.score.cpp, which isn't one of this unit's files" in res.stderr
    assert (root / "build.ninja").read_text() == before


def test_export_takes_the_xdk_whose_compiler_is_the_games(tmp_path):
    """With two XDKs set up, `ninja export` took the first one's linker and imagebld, not the game's XDK's."""
    root = _project(tmp_path)
    owns = []
    for build in ("4627", "5849"):
        xbox = tmp_path / f"xdk{build}" / "xbox"
        (xbox / "include").mkdir(parents=True)
        (xbox / "include" / "xtl.h").write_text("")
        (xbox / "lib").mkdir()
        (xbox / "lib" / "xboxkrnl.lib").write_bytes(b"")
        owns += ["--own", f"MSVC/xdk-{build}=none,{xbox / 'bin' / 'vc71' / 'CL.EXE'},{xbox / 'include'}"]
    cfg = root / "tools" / "configure.py"
    cfg.write_text(cfg.read_text().replace('MSVC_VERSION = "7.1"', 'MSVC_VERSION = "MSVC/xdk-5849"'))
    res = subprocess.run([sys.executable, "tools/configure.py", "v", "--msvc", str(root / "msvc"), *owns], cwd=root,
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    export = _ninja(root).split("rule export\n", 1)[1].split("\n", 2)[0]
    assert str(tmp_path / "xdk5849" / "xbox") in export and "xdk4627" not in export


def test_objects_depend_on_the_project_files_the_compiler_includes_and_nothing_else(tmp_path):
    root = _project(tmp_path)
    _entry(root)
    assert _configure(root).returncode == 0
    ninja = _ninja(root)
    for rule in ("cl_deps", "ltcg_cc"):
        block = ninja.split(f"rule {rule}\n", 1)[1].split("\nrule ", 1)[0]
        if rule == "cl_deps":  # (through tools/compile.py, which runs cldeps.py with /showIncludes)
            assert "tools/compile.py" in block and "--deps" in block
        else:
            assert "tools/cldeps.py $out --" in block and "/showIncludes" in block
        assert "deps = gcc" in block and "depfile = $out.d" in block
    assert "include/" not in ninja.split("build build/v/ltcg/u/01_u_drv.obj", 1)[1].split("\n", 1)[0]
    # Visual C++ 6 has no /showIncludes: its objects depend on every header, as before
    cfg = root / "tools" / "configure.py"
    cfg.write_text(cfg.read_text().replace('MSVC_VERSION = "7.1"', 'MSVC_VERSION = "6.0"'))
    assert _configure(root).returncode == 0
    old = _ninja(root)
    assert "/showIncludes" not in old.split("rule cl\n", 1)[1].split("\nrule ", 1)[0]
    # so neither do the harness's objects (an early XDK's Visual C++ 7.0 is the same): every header and harness file
    drv = old.split("build build/v/ltcg/u/01_u_drv.obj: ", 1)[1].split("\n", 1)[0]
    assert drv.startswith("ltcg_cc_all ") and "harness/u.drv.cpp" in drv and "cldeps" not in drv
    assert "/showIncludes" not in old.split("rule ltcg_cc_all\n", 1)[1].split("\nrule ", 1)[0]
    (root / "tools" / "configure.py").write_text(cfg.read_text().replace('"6.0"', '"7.1"'))
    # a stand-in compiler that reports includes as Visual C++ does: inside the project, and an SDK's outside it
    (root / "include" / "core").mkdir()
    (root / "include" / "core" / "maths.h").write_text("")
    (root / "src" / "auto" / "w.cpp").write_text("")
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    (sdk / "xtl.h").write_text("")
    (root / "harness" / "common").mkdir()
    (root / "harness" / "common" / "conv.h").write_text("")
    # a file found by the including file's own folder: wibo shows it by the host's / as drive Z:
    local = ("z:" + str(root).replace("/", "\\") if sys.platform != "win32" else str(root)) + "\\harness\\common/conv.h"
    fake = tmp_path / "cl.py"
    fake.write_text("import sys\nprint('u.drv.cpp')\nprint('Note: including file: include\\\\core/maths.h')\n"
                    f"print('Note: including file:  {sdk.as_posix()}\\\\xtl.h')\n"
                    f"print('Note: including file: ' + {local!r})\n"
                    "print('Note: including file:   harness/../src/auto/w.cpp')\nprint('warning C4100: x')\n"
                    "sys.exit(int(sys.argv[1]))\n")
    out = "build/v/ltcg/u/01_u_drv.obj"
    res = subprocess.run([sys.executable, "tools/cldeps.py", out, "--", sys.executable, str(fake), "0"], cwd=root,
                         capture_output=True, text=True)
    assert res.returncode == 0 and res.stdout.splitlines() == ["u.drv.cpp", "warning C4100: x"]
    assert (root / f"{out}.d").read_text() == f"{out}: include/core/maths.h harness/common/conv.h src/auto/w.cpp\n"
    (root / f"{out}.d").unlink()
    res = subprocess.run([sys.executable, "tools/cldeps.py", out, "--", sys.executable, str(fake), "2"], cwd=root,
                         capture_output=True, text=True)
    assert res.returncode == 2 and not (root / f"{out}.d").exists()
