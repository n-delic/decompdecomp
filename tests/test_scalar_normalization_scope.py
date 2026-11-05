"""Synthetic scalar normalization hypotheses and conservative span guards."""
import pytest

from decompnet.app import copy_lifetime_repairs, draft_repairs, scalar_normalization_scope

SOURCE = '''
#define CONVERT(out, value) {double d=(value); if(d<0)out=0;else out=(int)d;}
int normalize(int input, int width) {
    int offset;
    int result;
    int saved;
    offset=input;
    result=width;
    offset = (offset < 0) ? ((-offset) - result) : offset;
    return offset;
}
'''


def test_scoped_copy_preserves_unrelated_macro_and_reaches_first_page():
    candidates = scalar_normalization_scope.variants(SOURCE, 'normalize')
    assert len(candidates) == 1
    family, candidate = candidates[0]
    assert family == scalar_normalization_scope.FAMILY
    assert candidate.splitlines()[1] == SOURCE.splitlines()[1]
    assert 'do {saved=offset; if(offset<0){offset=0;saved=-saved-result;}} while(0); offset=saved;' in candidate
    assert (family, candidate) in copy_lifetime_repairs.variants(SOURCE, 'normalize')
    labels = {}
    page = draft_repairs.variants(SOURCE, 'normalize', 'mips', labels=labels, limit=40)
    assert candidate in page
    assert labels[candidate] == family
    assert family in copy_lifetime_repairs.FAMILIES
    assert family in copy_lifetime_repairs.hint(SOURCE, 'normalize')


@pytest.mark.parametrize('before,after', [
    ('((-offset) - result)', '(-(offset-result))'),
    ('offset < 0', 'offset < 0.0'),
    ('((-offset) - result)', '(-offset - mutate())'),
    ('((-offset) - result)', '(-offset - result++)'),
    ('int offset;', 'volatile int offset;'),
    ('int saved;', 'short saved;'),
    ('offset=input;', 'escape(&(offset)); offset=input;'),
    ('result=width;', 'escape(&(result)); result=width;'),
    ('result=width;', 'escape(&(saved)); result=width;'),
    ('int saved;', '{int saved;}'),
    ('return offset;', 'return offset+saved;'),
    ('#define CONVERT', '#define ALIAS offset\n#define CONVERT'),
    ('#define CONVERT', '#define ALIAS saved\n#define CONVERT'),
    ('#define CONVERT', '#define ALIAS \\\n offset\n#define CONVERT'),
    ('offset = (offset < 0)', 'if(input) offset = (offset < 0)'),
])
def test_unsupported_or_observable_rewrites_are_rejected(before, after):
    assert not scalar_normalization_scope.variants(SOURCE.replace(before, after), 'normalize')


def test_no_unused_compatible_scratch_means_no_candidate():
    assert not scalar_normalization_scope.variants(SOURCE.replace('int saved;', ''), 'normalize')


@pytest.mark.parametrize('header', [
    '#define s32 volatile int',
    'typedef volatile int s32;',
    'typedef const int s32;',
    'typedef volatile int base; typedef base s32;',
    '#define offset hidden',
    '#define saved hidden',
    '#define ALIAS offset',
])
def test_effective_header_qualifiers_and_macros_are_excluded(header):
    source = SOURCE.replace('int offset;', 's32 offset;').replace('int saved;', 's32 saved;')
    assert not scalar_normalization_scope.variants(source, 'normalize', header)
    assert not any(f == scalar_normalization_scope.FAMILY
                   for f, _ in copy_lifetime_repairs.variants(source, 'normalize', header))
    labels = {}
    draft_repairs.variants(source, 'normalize', 'mips', labels=labels, header=header, limit=None)
    assert scalar_normalization_scope.FAMILY not in labels.values()


def test_boolean_python_constant_is_not_a_c_integer_literal():
    source = SOURCE.replace('offset < 0', 'offset < False')
    assert not scalar_normalization_scope.variants(source, 'normalize', '#define False 1')
