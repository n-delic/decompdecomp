"""Generated-port-only qualifier compatibility, proved by the PC compiler rather than by guessed C types."""
import os
import subprocess

import pytest

from decompnet import toolchains
from decompnet.port.wasm import build
from decompnet.port.wasm.declarations import qualified_externs


def diagnostic(path, line=2, own="'int'", shared="'volatile s32' (aka 'volatile int')", name='g_ready'):
    return f"{path}:{line}:12: error: redeclaration of '{name}' with a different type: {own} vs {shared}\n"


def test_a_redundant_scalar_extern_defers_to_stronger_shared_qualifiers(tmp_path):
    path = tmp_path / 'work.c'
    source = '#include "common.h"\nextern int g_ready;\nint update(void) { return g_ready; }\n'
    result = qualified_externs(source, diagnostic(path), path)
    assert 'extern int g_ready' not in result and 'return g_ready' in result
    assert result.count('\n') == source.count('\n')
    assert qualified_externs(source, diagnostic(path, own="'s32' (aka 'int')"), path) == result


@pytest.mark.parametrize('source,own,shared,line', [
    ('extern int *g_ready;\n', "'int *'", "'volatile int *'", 1),
    ('extern int g_ready[4];\n', "'int[4]'", "'volatile int[4]'", 1),
    ('extern short g_ready;\n', "'short'", "'volatile int'", 1),
    ('extern volatile int g_ready;\n', "'volatile int'", "'int'", 1),
    ('#if PORT\nextern int g_ready;\n#endif\n', "'int'", "'volatile int'", 2),
    ('void f(void) {\nextern int g_ready;\n}\n', "'int'", "'volatile int'", 2),
    ('extern int g_ready, g_other;\n', "'int'", "'volatile int'", 1),
    ('extern int g_ready = 1;\n', "'int'", "'volatile int'", 1),
    ('#define DECL \\\nextern int g_ready;\nDECL\n', "'int'", "'volatile int'", 2),
])
def test_unproved_or_source_only_qualifiers_still_fail(source, own, shared, line, tmp_path):
    path = tmp_path / 'work.c'
    assert qualified_externs(source, diagnostic(path, line, own, shared), path) == source


def test_an_error_in_another_file_or_for_another_symbol_cannot_remove_a_line(tmp_path):
    path = tmp_path / 'work.c'
    source = '#include "common.h"\nextern int g_ready;\n'
    assert qualified_externs(source, diagnostic(tmp_path / 'common.h'), path) == source
    assert qualified_externs(source, diagnostic(path, name='g_other'), path) == source


def test_compiler_failure_restores_the_original_generated_source(tmp_path, monkeypatch):
    src, ll = tmp_path / 'work.c', tmp_path / 'work.ll'
    source = '#include "common.h"\nextern int g_ready;\n'
    src.write_text(source)
    calls = []
    def compile(*args, **kwargs):
        calls.append(src.read_text())
        if len(calls) == 1:
            return subprocess.CompletedProcess([], 1, '', diagnostic(src))
        raise subprocess.TimeoutExpired('compiler', 1)
    monkeypatch.setattr(build, '_run', compile)
    with pytest.raises(subprocess.TimeoutExpired):
        build.compile_ir('zig', src, ll, tmp_path, {}, lambda *args: None)
    assert src.read_text() == source and 'extern' not in calls[1]


@pytest.mark.skipif(toolchains.installed('zig') is None, reason='needs the pinned Zig compiler')
def test_the_pinned_port_compiler_accepts_the_qualified_scalar_and_keeps_a_volatile_load(tmp_path):
    src, ll = tmp_path / 'work.c', tmp_path / 'work.ll'
    (tmp_path / 'common.h').write_text('typedef int s32;\nextern volatile s32 g_ready;\n')
    source = '#include "common.h"\nextern int g_ready;\nint update(void) { return g_ready; }\n'
    src.write_text(source)
    result = build.compile_ir(toolchains.binary('zig'), src, ll, tmp_path, dict(os.environ), lambda *args: None)
    assert not result.returncode, result.stderr
    assert 'load volatile i32' in ll.read_text()
    assert src.read_text() == source
