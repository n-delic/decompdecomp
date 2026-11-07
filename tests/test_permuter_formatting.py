"""The native printer retains original line groups without inventing new ones."""
import pytest

from decompnet.permuter_formatting import LineGroups

parser = pytest.importorskip('pycparser').CParser
ORIGINAL = 'int f(void) { int x; x=1; x|=8; return x; }'
PRINTED = 'int f(void) {\n  int x;\n  x = 1;\n  x |= 8;\n  return x;\n}\n'


def groups(original=ORIGINAL, header=''):
    return LineGroups(original, header, 'f', parser().parse)


def test_unchanged_pair_retains_line_and_ast_idempotently():
    guard = groups()
    result = guard.apply(PRINTED)
    assert 'x = 1; x |= 8;' in result
    assert guard.apply(result) == result
    assert groups('int f(void) { int x; x=1;\n x|=8; return x; }').apply(PRINTED) == PRINTED


@pytest.mark.parametrize('candidate', [
    PRINTED.replace('x = 1;', 'x = 2;'),
    PRINTED.replace('  x = 1;\n', ''),
    PRINTED.replace('x = 1;\n  x |= 8;', 'x |= 8;\n  x = 1;'),
    PRINTED.replace('  return x;', '  x = 1;\n  x |= 8;\n  return x;'),
    PRINTED.replace('x = 1;\n', 'x = 1; /* keep this */\n'),
    PRINTED.replace('x = 1;\n', 'x = 1;\n#line 7\n'),
])
def test_changed_or_ambiguous_pairs_and_comments_stay_unchanged(candidate):
    assert groups().apply(candidate) == candidate


def test_multiline_comment_does_not_create_a_false_original_group():
    original = 'int f(void) { int x; x=1; /*\n original line break\n */ x|=8; return x; }'
    assert groups(original).apply(PRINTED) == PRINTED


def test_header_offsets_and_same_line_comments_keep_real_group():
    original = ORIGINAL.replace('x=1;', 'x=1; /* a comment */')
    assert 'x = 1; x |= 8;' in groups(original, '/* header\n */\ntypedef int s32;').apply(PRINTED)


def test_three_assignments_preserve_overlapping_boundaries():
    original = ORIGINAL.replace('x|=8;', 'x|=8; x+=2;')
    candidate = PRINTED.replace('x |= 8;', 'x |= 8;\n  x += 2;')
    assert 'x = 1; x |= 8; x += 2;' in groups(original).apply(candidate)


def test_duplicate_original_group_is_not_guessed():
    original = ORIGINAL.replace('return x;', 'x=1; x|=8; return x;')
    assert groups(original).apply(PRINTED) == PRINTED


def test_original_directive_line_does_not_merge_different_physical_lines():
    original = ORIGINAL.replace('x|=8;', '\n#define VALUE 8\n x|=8;')
    assert groups(original).apply(PRINTED) == PRINTED


def test_continuations_and_unreadable_context_disable_formatting_only():
    assert groups(ORIGINAL.replace('x=1;', 'x=\\\n1;')).apply(PRINTED) == PRINTED
    assert groups(header='not readable C!').apply(PRINTED) == PRINTED


@pytest.mark.parametrize('header', ['#define HERE __LINE__', '#define JOIN(a,b) a##b', '#include "unknown.h"'])
def test_line_sensitive_or_external_macros_disable_formatting(header):
    assert groups(header=header).apply(PRINTED) == PRINTED


def test_changed_verification_ast_returns_original_candidate():
    real = parser().parse
    calls = []
    def changing_parse(source):
        calls.append(source)
        return real(source.replace('return x;', 'return 3;') if len(calls) == 3 else source)
    guard = LineGroups(ORIGINAL, '', 'f', changing_parse)
    assert guard.apply(PRINTED) == PRINTED


def test_native_generator_preserves_unchanged_input_group():
    from decompnet import toolchains
    from decompnet.app import permute
    if not toolchains.installed(permute.TOOL):
        pytest.skip('needs pinned decomp-permuter')
    source = 'int f(int n) { int a,b,c; a=n; a|=8;\n b=n+3;\n c=b*5;\n return a+c; }'
    generator = permute.Generator()
    try:
        rows = generator.batch({'header':'', 'source':source, 'function':'f',
                                'family':'gcc', 'seed':0, 'count':64})
        assert rows is not None
        assert any('a = n; a |= 8;' in row for row in rows)
        assert not any('a = n;\n  a |= 8;' in row for row in rows)
    finally:
        generator.close()
