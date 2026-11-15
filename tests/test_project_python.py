"""Imported projects keep their Python dependencies separate from the app's runtime."""
import json
import os
import sys
import venv

import pytest

from decompnet.forks.project import ForkProject


@pytest.mark.parametrize('name', ['.venv', 'venv'])
def test_project_python_used_directly_and_by_child_tools(tmp_path, name):
    folder = tmp_path / name
    venv.EnvBuilder(with_pip=False).create(folder)
    (tmp_path / '.decomp.wtf.json').write_text(json.dumps({'platform': 'n64'}))
    script = "import os,sys; assert sys.prefix == os.environ['VIRTUAL_ENV']; print(sys.prefix)"
    project = ForkProject(tmp_path)
    ok, output = project.run(['python3', '-c', script])
    assert ok and str(folder) in output, output
    child = 'python.exe' if os.name == 'nt' else 'python3'
    # Windows CreateProcess searches the parent executable's directory before PATH, even
    # with a virtualenv in the child environment. Resolve explicitly as build tools do;
    # otherwise this checks Windows process lookup, not the project's PATH selection.
    launcher = (f"import shutil,subprocess; "
                f"subprocess.run([shutil.which({child!r}), '-c', {script!r}], check=True)")
    ok, output = project.run([sys.executable, '-c', launcher])
    assert ok and str(folder) in output, output


def test_incomplete_environment_is_not_selected(tmp_path):
    (tmp_path / '.venv').mkdir()
    (tmp_path / '.venv/pyvenv.cfg').touch()
    (tmp_path / '.decomp.wtf.json').write_text(json.dumps({'platform': 'n64'}))
    ok, _ = ForkProject(tmp_path).run(['python3', '-c', 'print("fallback")'])
    assert ok


def test_failed_build_keeps_early_error_after_verbose_output(tmp_path):
    (tmp_path / '.decomp.wtf.json').write_text(json.dumps({'platform': 'n64'}))
    script = "print('error: missing prerequisite'); [print('progress', i) for i in range(1500)]; exit(1)"
    logs = []
    ok, output = ForkProject(tmp_path).run(['python3', '-c', script], log=logs.append)
    assert not ok
    assert 'error: missing prerequisite' in output
    assert len(output.splitlines()) <= 72
