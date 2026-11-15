"""Synthetic scalar normalization hypotheses and conservative span guards."""
import pytest

from decompnet.app import draft_repairs
from decomprepair import copy_lifetime_repairs, scalar_normalization_scope

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


def test_generated_scope_preserves_defined_c90_boundary_results(tmp_path):
    import shutil
    import subprocess

    cc = shutil.which('cc')
    if not cc:
        pytest.skip('host C compiler unavailable')
    candidate = scalar_normalization_scope.variants(SOURCE, 'normalize')[0][1]
    original = SOURCE.replace('normalize(', 'original(')
    alternate = candidate[candidate.index('int normalize'):].replace('normalize(', 'alternate(')
    program = original + '\n' + alternate + '''
int main(void) {
    int cases[][2] = {
        {-2147483647, 1}, {-2147483647, 2147483647},
        {-50, -20}, {-50, 100}, {-50, 2147483647},
        {0, 2147483647}, {123, (-2147483647 - 1)},
        {2147483647, (-2147483647 - 1)}, {-32768, -32768},
        {-10, (-2147483647 - 1) + 11}
    };
    unsigned int i;
    for (i = 0; i < sizeof(cases)/sizeof(cases[0]); ++i)
        if (original(cases[i][0], cases[i][1]) != alternate(cases[i][0], cases[i][1]))
            return 1;
    return 0;
}
'''
    source = tmp_path / 'normalization.c'
    source.write_text(program)
    executable = tmp_path / 'normalization'
    subprocess.run([cc, '-std=c90', '-O2', str(source), '-o', str(executable)],
                   check=True, capture_output=True, timeout=20)
    subprocess.run([str(executable)], check=True, capture_output=True, timeout=10)
