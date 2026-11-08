import ctypes
import subprocess

import pytest

from decompnet.app.byte_increment_repairs import FAMILY, variants

HEADER = '''typedef signed char s8; typedef unsigned char u8;
typedef unsigned short u16; typedef int s32;
typedef struct { u8 pad[3]; u8 current; } League;
extern void *state; extern u8 *record; extern League *league;
'''


LOAD = '(*(s8 *)((char *)(state) + 3))'


STORE = LOAD+' = (s8)(cached + 1);'


BODY = '''s8 cached; u8 team;
cached = '''+LOAD+''';
team = league->current;
if (cached >= ((*(u16 *)((char *)(record) + 2))-1)) { goto end; }
'''+STORE+'''
end: return cached + team;
'''


def source(body=BODY, signature='void'):
    return 'int demo('+signature+') {\n'+body+'\n}\nint neighbour(void){return 9;}'


def propose(text, header=HEADER):
    return variants(text, 'demo', header, 'mips')


def test_cached_byte_crosses_read_and_conditional_goto_preserving_cached_value():
    text = source()
    out = propose(text)
    assert len(out) == 2
    assert all(f == FAMILY for f, _ in out)
    assert '++((unsigned char *)state)[3];' in out[0][1]
    assert '((unsigned char *)state)[3]++;' in out[1][1]
    for _, candidate in out:
        assert candidate == text.replace(STORE, '++((unsigned char *)state)[3];') or candidate == text.replace(STORE, '((unsigned char *)state)[3]++;')
        assert 'return cached + team;' in candidate  # Cached value remains the old byte.
        assert candidate.endswith('int neighbour(void){return 9;}')


@pytest.mark.parametrize('body', [
    BODY.replace('team = league->current;', 'touch();'),
    BODY.replace('team = league->current;', 'team = touch();'),
    BODY.replace('team = league->current;', 'state = other;'),
    BODY.replace('team = league->current;', '*(u8 *)alias = 0;'),
    BODY.replace('team = league->current;', '((u8 *)state)[3] = 0;'),
    BODY.replace('team = league->current;', 'cached++;'),
    BODY.replace('team = league->current;', 'cached = 1;'),
    BODY.replace('team = league->current;', 'cached += 1;'),
    BODY.replace('cached >=', '(cached = 2) >='),
    BODY.replace('cached >=', 'touch() >='),
    BODY.replace('s8 cached;', 's8 cached; s8 *escape = &cached;'),
    BODY.replace('s8 cached;', 's8 cached; s8 *escape = &(cached);'),
    BODY.replace('s8 cached;', 's8 cached; Alias cached;'),
    BODY.replace('s8 cached;', 's8 cached; Alias *cached;'),
    BODY.replace('u8 team;', 'u8 team; Alias state;'),
    BODY.replace('u8 team;', 'u8 team; Alias *state;'),
    BODY.replace('cached = '+LOAD+';', 'if (flag) goto increment; cached = '+LOAD+'; increment:'),
    BODY.replace('cached = '+LOAD+';', 'if (flag) cached = '+LOAD+'; else cached = '+LOAD+';'),
    BODY.replace('cached = '+LOAD+';', 'cached = (*(s8 *)((char *)state+4));'),
    BODY.replace(STORE, 'consume('+STORE[:-1]+');'),
    BODY.replace(STORE, 'return '+STORE),
    BODY.replace(STORE, 'team = (u8)('+STORE[:-1]+');'),
    BODY.replace(STORE, LOAD+' = (s8)(cached + 2);'),
    BODY.replace(STORE, LOAD+' = (s16)(cached + 1);'),
    BODY.replace(STORE, '++'+LOAD+';'),
    BODY.replace('cached = '+LOAD+';', 'again: cached = '+LOAD+';').replace('end: return', 'goto again; end: return'),
    BODY.replace('team = league->current;', 'while(flag) { team=1; }'),
    BODY.replace('team = league->current;', 'for (;flag;) {}'),
    BODY.replace('team = league->current;', 'do {} while(flag);'),
    BODY.replace('team = league->current;', 'escape(&state);'),
    BODY.replace('team = league->current;', 'team = UNKNOWN;'),
])
def test_effects_aliases_unknown_control_and_used_assignment_results_rejected(body):
    assert propose(source(body)) == []


@pytest.mark.parametrize('header', [
    HEADER.replace('extern void *state;', 'extern volatile u8 *state;'),
    HEADER.replace('extern void *state;', 'extern u8 *volatile state;'),
    HEADER.replace('extern void *state;', 'typedef volatile u8 Byte; extern Byte *state;'),
    HEADER.replace('extern void *state;', 'typedef _Atomic u8 Byte; extern Byte *state;'),
    HEADER.replace('u8 current;', 'volatile u8 current;'),
    HEADER.replace('extern League *league;', 'extern volatile League *league;'),
    HEADER+'\n#define League Opaque\n',
    HEADER+'\n#define cached volatile_source\n',
    HEADER+'\n#define state next_state()\n',
    HEADER+'\n#define s8 signed char\n',
    HEADER+'\nextern int state;\n',
    HEADER+'\nextern volatile void *state;\n',
    HEADER+'\n#if FLAG\nextern int conflict;\n#endif\n',
])
def test_qualified_conflicting_or_macro_declarations_rejected(header):
    assert propose(source(), header) == []


def test_unknown_architecture_and_unknown_byte_typedef_rejected():
    assert variants(source(), 'demo', HEADER, None) == []
    assert propose(source(), HEADER.replace('typedef signed char s8;', 'typedef short s8;')) == []


@pytest.mark.parametrize('statement', [
    'p[3] = p[3] + 1;',
    'p[3] = (u8)(p[3] + 1);',
    '(*(s8 *)((char *)p+3)) = (s8)((*(s8 *)((char *)p+3))+1);',
])
def test_direct_byte_increment_ordinary_pointer_parameter(statement):
    out = propose(source(statement+' return 0;', 'u8 *p'))
    assert len(out) == 2
    assert '++((unsigned char *)p)[3];' in out[0][1]


@pytest.mark.parametrize('statement', [
    'p[next()] = p[next()] + 1;',
    'p[i++] = p[i++] + 1;',
    '*p++ = *p++ + 1;',
    'next()[3] = next()[3] + 1;',
    'p[3] = p[4] + 1;',
    'p[3] = (u8)(consume(p[3]) + 1);',
    'p[3] = (u8)(p[3] + x);',
    'return (p[3] = p[3] + 1);',
])
def test_direct_effectful_or_different_addresses_rejected(statement):
    assert propose(source(statement, 'u8 *p')) == []


def test_volatile_parameter_shadow_not_treated_as_plain_scalar():
    header = HEADER+'typedef volatile int V;'
    body = BODY.replace('team = league->current;', 'V x; if (x) goto end;')
    assert propose(source(body, 'int x'), header) == []


def test_host_all_256_bytes_branches_cached_value_and_evaluation_counts(tmp_path):
    # Controlled host semantics regression only. Pinned game matching uses its own compiler.
    cc = '/usr/bin/cc'
    from pathlib import Path
    if not Path(cc).exists():
        pytest.skip('needs system C compiler for controlled host regression')
    before = '''int demo(unsigned char *p, int flag) {
    s8 cached; int observed;
    before_count++;
    cached = (*(s8 *)((char *)p + 3));
    observed = flag;
    if (flag) goto end;
    (*(s8 *)((char *)p + 3)) = (s8)(cached + 1);
    end: after_count++; return cached * 4 + observed;
    }'''
    out = variants(before, 'demo', HEADER, 'mips')
    assert len(out) == 2
    bodies = [before.replace('demo(', 'original(')] + [s.replace('demo(', f'candidate{i}(') for i, (_,s) in enumerate(out)]
    # Direct expression has two ordinary pointer reads originally and one in ++; neither
    # pointer lookup has side effects. Effectful address expressions are refused above.
    code = 'typedef signed char s8; int before_count, after_count;\n'+ '\n'.join(bodies)
    cpath, so = tmp_path/'byte.c', tmp_path/'byte.so'
    cpath.write_text(code)
    subprocess.run([cc, '-shared', '-fPIC', '-std=c99', '-fsanitize=undefined', str(cpath), '-o', str(so)], check=True, capture_output=True)
    lib = ctypes.CDLL(str(so))
    for byte in range(256):
        for flag in (0, 1):
            expected = None
            for name in ('original', 'candidate0', 'candidate1'):
                fn = getattr(lib, name)
                fn.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int]
                fn.restype = ctypes.c_int
                data = (ctypes.c_ubyte*8)(*([0]*3+[byte]+[0]*4))
                before_count = ctypes.c_int.in_dll(lib, 'before_count')
                after_count = ctypes.c_int.in_dll(lib, 'after_count')
                before_count.value = after_count.value = 0
                result = fn(data, flag)
                assert (before_count.value, after_count.value) == (1, 1)
                measured = result, tuple(data)
                if expected is None:
                    expected = measured
                    assert data[3] == (byte if flag else (byte+1)%256)
                    assert result == (byte if byte < 128 else byte-256)*4+flag
                assert measured == expected


def test_parenthesized_escape_of_intervening_scalar_cannot_alias_stored_byte():
    body = BODY.replace('cached = '+LOAD+';', 'state = &( (team)); cached = '+LOAD+';')
    assert propose(source(body)) == []
    body = BODY.replace('team = league->current;', 'team = flag;').replace('s8 cached;', 's8 cached; escape(&( (flag)));')
    assert propose(source(body, 'int flag')) == []


def test_pointer_object_escape_with_nested_parentheses_rejected():
    body = BODY.replace('cached = '+LOAD+';', 'escape(&( (state))); cached = '+LOAD+';')
    assert propose(source(body)) == []


def test_c_octal_constant_is_not_decimal_or_host_python_octal():
    statement = 'p[010] = p[010] + 1; return 0;'
    out = propose(source(statement, 'u8 *p'))
    assert len(out) == 2
    assert '++((unsigned char *)p)[8];' in out[0][1]
    assert propose(source('p[08] = p[08] + 1;', 'u8 *p')) == []


def test_keywords_introduced_in_candidate_cannot_be_macros():
    for keyword in ('unsigned', 'char'):
        assert propose(source('p[3] = p[3]+1;', 'u8 *p'), HEADER+'\n#define '+keyword+' OPAQUE\n') == []


def test_pinned_compiler_generated_source_matches_independent_increment_control(tmp_path):
    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned compiler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                          'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2', '-g3']}})
    before = '''int demo(unsigned char *p, int flag) {
    signed char cached; cached = (*(signed char *)((char *)p+3));
    if(flag) goto done;
    (*(signed char *)((char *)p+3)) = (signed char)(cached+1);
    done: return cached;
    }'''
    control = before.replace('(*(signed char *)((char *)p+3)) = (signed char)(cached+1);',
                             '++((unsigned char *)p)[3];')
    expected = compile_source(cfg, '', control)
    assert expected.ok, expected.log
    code = extract_function(expected.obj, 'demo').code
    choices = variants(before, 'demo', '', 'mips')
    assert len(choices) == 2
    for _, text in choices:
        result = compile_source(cfg, '', text)
        assert result.ok, result.log
        assert extract_function(result.obj, 'demo').code == code


def test_scalar_parameter_shadowing_global_pointer_is_not_an_address_proof():
    body = BODY.replace('team = league->current;', 'state = other;')
    assert propose(source(body, 'int state, int other')) == []
