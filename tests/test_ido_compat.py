"""A GCC-shaped draft becomes buildable by IDO where only `void *` arithmetic stopped it (ido_compat)."""
import pytest

from decompnet import toolchains
from decompnet.app import ido_compat

DRAFT = '''s32 f(s32 arg0, void *arg1) {
    void *var_v0;
    void *kept;
    s32 x;

    var_v0 = arg1;
    kept = arg1;
    x = *(u8 *)((char *)(var_v0) + 5);
    var_v0 += 7;
    return x + *(u8 *)((char *)(kept) + 1) + (s32) kept;
}
'''


def test_a_stepped_void_cursor_becomes_a_char_cursor_for_ido_only():
    out = ido_compat.adapted('ido-7.1', DRAFT)
    assert '    char *var_v0;' in out and '    void *kept;' in out  # (kept is never stepped)
    assert 'var_v0 += 7;' in out and out.count('void') == DRAFT.count('void') - 1
    for other in ('gcc-2.7.2-kmc', 'sn-n64-281', 'gcc-2.8.1-pm'):
        assert ido_compat.adapted(other, DRAFT) == DRAFT
    assert ido_compat.adapted('ido-5.3', 'int f(void) { return 0; }\n') == 'int f(void) { return 0; }\n'


def test_pre_and_post_steps_count_too():
    for step in ('++var_v0;', 'var_v0++;', '--var_v0;', 'var_v0 -= 2;'):
        assert 'char *var_v0;' in ido_compat.adapted('ido-5.3', DRAFT.replace('var_v0 += 7;', step))


def test_ido_builds_what_it_refused():
    if not toolchains.installed('ido-7.1'):
        pytest.skip('The pinned IDO compiler is not installed; never download it here.')
    import tempfile
    from pathlib import Path

    from decompnet import consoles
    from decompnet.compiler import compile_source
    from decompnet.project import compiler_config
    cfgd = {'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': []}}
    header = 'typedef unsigned char u8;\ntypedef int s32;\n'
    with tempfile.TemporaryDirectory() as work:
        cfg = compiler_config(Path(work), {'compiler': {'toolchain': 'ido-7.1',
                                                        'flags': consoles.default_flags(cfgd, 'ido-7.1')}})
        raw = compile_source(cfg, '', header + DRAFT)
        assert not raw.ok and 'Bad operand type' in raw.log
        fixed = compile_source(cfg, '', header + ido_compat.adapted('ido-7.1', DRAFT))
        assert fixed.ok, fixed.log
