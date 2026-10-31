"""Build diagnostics follow observed errors and never guess compiler versions or edit a checkout."""

import sys
from types import SimpleNamespace

import pytest

from decompnet.forks import buildhelp
from decompnet.forks.project import ForkProject


@pytest.mark.parametrize('output', [
    'make: ./tools/mips64-elf-cpp: No such file or directory',
    '/bin/sh: line 1: mips64-elf-cpp: command not found',
    'mips64-elf-cpp: not found',
    "'mips64-elf-cpp.exe' is not recognized as an internal or external command",
])
def test_cross_preprocessor_forms_work_for_make_ninja_and_no_makefile(tmp_path, output):
    for executable in ('make', 'make.exe', 'mingw32-make.exe', 'ninja'):
        hint = buildhelp.explain(tmp_path, executable, output)
        assert 'Missing cross preprocessor: mips64-elf-cpp' in hint
        assert 'not binutils' in hint


def test_old_make_and_declared_compiler_need_matching_failure_output(tmp_path, monkeypatch):
    make = 'DUMMY != mkdir -p build/asm\nCC = tools/ido/cc\n'
    (tmp_path / 'Makefile').write_text(make)
    monkeypatch.setattr(buildhelp.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='GNU Make 3.81\n'))
    assert buildhelp.explain(tmp_path, 'make', 'src/game.c:3:1: error: expected ;') == ''
    result = buildhelp.explain(tmp_path, 'make.exe', 'make: tools/ido/cc: No such file or directory')
    assert 'Check the GNU Make version' in result
    assert 'tools/ido/cc' in result
    assert 'IDO 5.3' not in result and 'IDO 7.1' not in result
    assert (tmp_path / 'Makefile').read_text() == make


def test_inactive_make_branch_does_not_report_missing_compiler(tmp_path):
    (tmp_path / 'Makefile').write_text('ifeq ($(OLD),1)\nCC = tools/old/cc\nelse\nCC = tools/cc\nendif\n')
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'tools/cc').touch()
    assert buildhelp.explain(tmp_path, 'make', 'src/game.c: error: expected ;') == ''


def test_diagnostic_probe_failure_retains_missing_tool_hint(tmp_path, monkeypatch):
    (tmp_path / 'Makefile').write_text('DUMMY != true\n')

    def fail(*a, **k):
        raise OSError('unavailable')

    monkeypatch.setattr(buildhelp.subprocess, 'run', fail)
    result = buildhelp.explain(tmp_path, 'mingw32-make.exe', 'make: mips-linux-gnu-cpp: command not found')
    assert 'Missing cross preprocessor' in result
    assert 'GNU Make 4.0' not in result


def test_project_run_appends_diagnostic_after_output(tmp_path):
    script = tmp_path / 'fail.py'
    script.write_text("print('/bin/sh: line 1: mips-linux-gnu-cpp: command not found')\nraise SystemExit(1)\n")
    lines = []
    ok, tail = ForkProject(tmp_path).run([sys.executable, str(script)], log=lines.append)
    assert not ok
    assert 'mips-linux-gnu-cpp: command not found' in tail
    assert tail.index('command not found') < tail.index('Missing cross preprocessor:')
    assert 'Missing cross preprocessor:' in lines[-1]
