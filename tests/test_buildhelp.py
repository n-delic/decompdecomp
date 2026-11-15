"""Build diagnostics identify prerequisites without guessing compiler versions or changing a checkout."""

from types import SimpleNamespace

from decompnet.forks import buildhelp


def test_old_make_cross_cpp_and_missing_local_compiler(tmp_path, monkeypatch):
    make = 'DUMMY != mkdir -p build/asm\nCC = tools/ido/cc\nCPP := mips64-elf-cpp -P\n'
    (tmp_path / 'Makefile').write_text(make)
    monkeypatch.setattr(buildhelp.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='GNU Make 3.81\n'))
    result = buildhelp.explain(tmp_path, '/usr/bin/make', 'make: mips64-elf-cpp: No such file or directory\n')
    assert 'GNU Make 4.0 or newer' in result
    assert 'Missing cross preprocessor: mips64-elf-cpp' in result
    assert 'not binutils' in result
    assert 'tools/ido/cc' not in result  # a Make variable may belong to an inactive branch
    missing = buildhelp.explain(tmp_path, '/usr/bin/make', 'tools/ido/cc: No such file or directory\n')
    assert 'tools/ido/cc' in missing
    assert 'IDO 5.3' not in result and 'IDO 7.1' not in result
    assert (tmp_path / 'Makefile').read_text() == make
    assert sorted(p.name for p in tmp_path.iterdir()) == ['Makefile']


def test_modern_make_existing_compiler_and_unrelated_failures(tmp_path, monkeypatch):
    (tmp_path / 'Makefile').write_text('DUMMY != echo ready\nCC = tools/cc\n')
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'tools/cc').touch()
    monkeypatch.setattr(buildhelp.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='GNU Make 4.4.1\n'))
    assert buildhelp.explain(tmp_path, 'gmake', 'syntax error') == ''
    assert buildhelp.explain(tmp_path, 'ninja', 'syntax error') == ''


def test_diagnostic_probe_failure_retains_missing_tool_hint(tmp_path, monkeypatch):
    (tmp_path / 'Makefile').write_text('DUMMY != true\n')
    def fail(*a, **k):
        raise OSError('unavailable')
    monkeypatch.setattr(buildhelp.subprocess, 'run', fail)
    result = buildhelp.explain(tmp_path, 'make', 'make: mips-linux-gnu-cpp: Command not found')
    assert 'Missing cross preprocessor' in result
    assert 'GNU Make 4.0' not in result
