"""The app compiles the repair library itself: kernels before it is imported, the plain modules in the background.

A fake native_build.py stands in for the library's, so no test compiles anything."""
import subprocess
import sys

import pytest

from decompnet.app import native_library

# modules are "built" by writing <name>.built; every run is recorded
FAKE = '''import sys
from pathlib import Path
package = Path(sys.argv[sys.argv.index("--package") + 1])
with open(package / "runs", "a") as runs:
    runs.write(" ".join(sys.argv[3:]) + "\\n")
names = ["kernel", "one", "two", "three"] if "--all" in sys.argv else ["kernel"]
if "--only" in sys.argv:
    names = sys.argv[sys.argv.index("--only") + 1].split(",")
names = [name for name in names if not (package / (name + ".built")).exists()]
if "--list-missing" in sys.argv:
    print("\\n".join(names))
elif "--missing" in sys.argv:
    for name in names:
        # without linking, a plain module is only compiled: still missing until the library is linked
        (package / (name + (".object" if "--no-link" in sys.argv else ".built"))).touch()
'''


@pytest.fixture
def package(tmp_path, monkeypatch):
    folder = tmp_path / "decomprepair"
    folder.mkdir()
    (folder / "native_build.py").write_text(FAKE)
    monkeypatch.delenv("DECOMP_NATIVE_LIBRARY", raising=False)
    monkeypatch.setattr(native_library, "_has_c_compiler", lambda: True)
    monkeypatch.setattr(native_library.importlib.util, "find_spec", lambda name: object())
    return folder


def built(package):
    return sorted(path.stem for path in package.glob("*.built"))


def test_kernels_are_built_once(package):
    assert native_library.ensure_kernels(package)
    assert built(package) == ["kernel"]
    assert native_library.ensure_kernels(package)
    assert (package / "runs").read_text().splitlines().count("--missing") == 1


def test_the_rest_is_compiled_in_the_background_after_the_kernels(package):
    native_library.ensure_kernels(package)
    compiler = native_library.Compiler(package, delay=0, jobs=1)
    compiler.start()
    compiler.thread.join(60)
    assert built(package) == ["kernel", "one", "three", "two"]
    runs = (package / "runs").read_text().splitlines()
    assert [run for run in runs if "--only" in run and "--no-link" not in run] == []
    assert runs[-2] == "--all --missing"  # linked once, after the batches; then what is left is read
    assert compiler.snapshot() is None  # no row once it is done


def test_without_kernels_nothing_else_is_tried(package):
    compiler = native_library.Compiler(package, delay=0)
    compiler.start()
    compiler.thread.join(60)
    assert built(package) == []


def test_stop_ends_it_and_the_row_says_so(package):
    compiler = native_library.Compiler(package, delay=30)
    compiler.total, compiler.done = 4, 1
    assert compiler.snapshot()["state"] == "running" and "1 of 4" in compiler.snapshot()["detail"]
    compiler.stop()
    assert compiler.snapshot()["state"] == "stopping"
    compiler.start()
    compiler.thread.join(10)
    assert not compiler.thread.is_alive()


@pytest.mark.parametrize("reason", ["opt out", "old library", "bundle", "no compiler"])
def test_copies_that_cannot_compile_are_left_alone(package, tmp_path, monkeypatch, reason):
    if reason == "opt out":
        monkeypatch.setenv("DECOMP_NATIVE_LIBRARY", "0")
    elif reason == "old library":
        (package / "native_build.py").write_text("raise SystemExit('would remove every build')\n")
    elif reason == "bundle":
        package = package.rename(tmp_path / "x.app")
    else:
        monkeypatch.setattr(native_library, "_has_c_compiler", lambda: False)
    assert native_library.unavailable(package)
    assert not native_library.ensure_kernels(package)
    assert not (package / "runs").exists()


def test_the_app_builds_the_kernels_before_anything_imports_the_library():
    code = ("import sys\n"
            "from decompnet.app import cli, native_library\n"
            "def kernels():\n"
            "    print(sorted(name for name in sys.modules if name.split('.')[0] == 'decomprepair'))\n"
            "    raise SystemExit(0)\n"
            "native_library.ensure_kernels = kernels\n"
            "cli.run(None)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
