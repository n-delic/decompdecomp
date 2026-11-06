import shutil
import subprocess

import pytest

from decompnet import byte_field_macros, m2cfix

W = '#define W(p,o) (*(int*)((char*)(p)+(o)))\n'
P = '#define P(p,o) (*(char**)((char*)(p)+(o)))\n'


def test_nested_field_loads_and_assignment_lvalues_become_plain_memory_expressions():
    source = W+P+'void f(char *a) { W(P(a,4),8)=W(a,12); }'
    result = m2cfix.expand_macros(source)
    body = result[result.index('void f'):]
    assert 'W(' not in body and 'P(' not in body
    assert '(*(int *)' in body and '(*(char* *)' in body
    assert result.count('\n') == source.count('\n')
    assert result.startswith(W+P)


def test_definition_in_header_and_alternative_macro_spelling():
    header = '#define READ(base,offset) (*(unsigned short *)((unsigned char *)(base) + (offset)))\n'
    result = byte_field_macros.expand('int f(char *p) { return READ(p,2); }', header)
    assert 'READ(' not in result
    assert 'unsigned short' in result and 'unsigned char' in result


def test_comments_literals_and_directives_are_not_invocations():
    source = W+'#define ALIAS W(a,4)\nchar *s="W(a,4)"; /* W(a,4) */\nint f(char *a) { return W(a,4); }'
    result = byte_field_macros.expand(source)
    assert '#define ALIAS W(a,4)' in result
    assert '"W(a,4)"' in result and '/* W(a,4) */' in result
    assert 'return W(' not in result


def test_calls_before_the_source_definition_stay_calls():
    source = 'int f(char *p) { return W(p,4); }\n'+W+'int g(char *p) { return W(p,4); }'
    result = byte_field_macros.expand(source)
    assert 'return W(p,4)' in result
    assert result.count('return W(p,4)') == 1


def test_parenthesized_comma_arguments_preserve_single_evaluation():
    source = W+'int f(char *p,int *n) { return W(((*n)++,p),((*n)++,4)); }'
    result = byte_field_macros.expand(source)
    assert 'return W(' not in result
    assert result.count('(*n)++') == 2


@pytest.mark.parametrize('definition', [
    '#define W(p,o) (*(int*)((char*)(p)+(o)+1))\n',
    '#define W(p,o) (*(int*)((short*)(p)+(o)))\n',
    '#define W(p,o) (use(p),*(int*)((char*)(p)+(o)))\n',
    '#define W(p,o) (*(int*)((char*)(p)+(o)+(o)))\n',
    '#define W(p,p) (*(int*)((char*)(p)+(p)))\n',
    '#define W (p,o) (*(int*)((char*)(p)+(o)))\n',
    '#define W(p,o) "(*(int*)((char*)(p)+(o)))"\n',
])
def test_unknown_or_different_templates_are_not_guessed(definition):
    source = definition+'int f(char *p) { return W(p,4); }'
    assert byte_field_macros.expand(source) == source


@pytest.mark.parametrize('prefix', [
    '#if MODE\n'+W+'#endif\n',
    W+W,
    W+'#undef W\n',
    W+'\f#undef W\n',
    W+'\v#if HIDDEN\n#undef W\n#endif\n',
    W+'#line 22 "x.c"\n',
    '#define int another_type\n'+W,
    W+'int line=__LINE__;\n',
    W+'const char *file=__FILE__;\n',
    W+'int counter=__COUNTER__;\n',
    W+'int column=__builtin_COLUMN();\n',
    W+'__pragma(message("opaque"))\n',
    '#define W(p,o) \\\n (*(int*)((char*)(p)+(o)))\n',
])
def test_ambiguous_preprocessing_and_observers_stay_opaque(prefix):
    source = prefix+'int f(char *p) { return W(p,4); }'
    assert byte_field_macros.expand(source) == source


def test_multiline_invocations_and_incorrect_argument_counts_stay_opaque():
    for call in ['W(p,\n4)', 'W(p,4,8)', 'W(p)', 'W(array[i,j],4)', 'W(p,4']:
        source = W+'int f(char *p) { return '+call+'; }'
        assert byte_field_macros.expand(source) == source


def test_duplicate_header_and_source_definitions_are_not_assumed_identical():
    source = W+'int f(char *p) { return W(p,4); }'
    assert byte_field_macros.expand(source, W) == source


def test_native_macro_expansion_matches_cpp_tokens_with_lvalues_and_side_effects(tmp_path):
    cc = shutil.which('clang')
    if not cc:
        pytest.skip('clang unavailable')
    source = W+P+'''int f(char *p,int *n) {
 W(p,((*n)++,4))=W(((*n)++,p),8);
 return W(P(p,16),0);
}'''
    expanded = byte_field_macros.expand(source)
    # cpp tokenization, not whitespace, is the semantic expansion boundary.
    import re
    def tokens(text):
        return re.findall(r'[A-Za-z_]\w*|\d+|\+\+|--|->|\S', text)
    outputs = []
    for name, text in [('original', source), ('expanded', expanded)]:
        path = tmp_path/(name+'.c')
        path.write_text(text)
        result = subprocess.run([cc, '-E', '-P', str(path)], text=True, capture_output=True, check=True)
        outputs.append(tokens(result.stdout))
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize('macro', [
    '#define STRINGIFY(x) #x\n',
    '#define JOIN(a,b) a##b\n',
])
def test_stringification_and_pasting_do_not_expand_arguments_early(macro):
    source = W+macro+'char *s=STRINGIFY(W(p,4));'
    assert byte_field_macros.expand(source) == source
    assert byte_field_macros.expand(W+'char *s=STRINGIFY(W(p,4));', macro) == W+'char *s=STRINGIFY(W(p,4));'
