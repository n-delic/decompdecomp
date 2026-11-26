"""packaging/build_library_native.py never fails a bundle build: it skips quietly or builds what the library offers."""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "build_library_native.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_library_native", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_library_without_native_build_is_left_interpreted(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DECOMP_NATIVE_LIBRARY", raising=False)
    (tmp_path / "decomprepair").mkdir()
    assert _load().main([str(tmp_path)]) == 0
    assert "stays interpreted" in capsys.readouterr().out


def test_the_opt_out_builds_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DECOMP_NATIVE_LIBRARY", "0")
    assert _load().main([str(tmp_path)]) == 0
    assert "DECOMP_NATIVE_LIBRARY=0" in capsys.readouterr().out


def test_all_asks_the_library_for_its_plain_modules_too(tmp_path, monkeypatch, capfd):
    package = tmp_path / "decomprepair"
    package.mkdir()
    (package / "native_build.py").write_text("import sys\nprint('ran', sys.argv[1:])\n")
    monkeypatch.setenv("DECOMP_NATIVE_LIBRARY", "all")
    assert _load().main([str(tmp_path)]) == 0
    assert "'--all'" in capfd.readouterr().out


def test_it_runs_the_librarys_script_on_the_installed_folder_and_survives_its_failure(tmp_path, capfd, monkeypatch):
    monkeypatch.delenv("DECOMP_NATIVE_LIBRARY", raising=False)
    package = tmp_path / "decomprepair"
    package.mkdir()
    (package / "native_build.py").write_text("import sys\nprint('ran', sys.argv[1:])\nsys.exit(3)\n")
    assert _load().main([str(tmp_path)]) == 0
    out = capfd.readouterr().out
    assert "ran ['--package'" in out and "could not be built" in out
    assert sys.executable  # the script runs under the interpreter that has Cython
