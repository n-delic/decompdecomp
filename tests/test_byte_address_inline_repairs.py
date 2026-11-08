import ctypes
import os
import re
import shutil
import subprocess

import pytest

from decompnet.app import byte_address_inline_repairs as repairs
from decompnet.app import debug_boundary_repairs

HEADER = 'typedef unsigned char u8; typedef signed short s16; void ping(void *); void pong(void *);'


SOURCE = '''int demo(void *ptr, int flag) {
    int a; int b; int out;
    a=1; b=2; out=0;
    if (*(s16 *)((u8 *)ptr + 2)) {
        if (flag) out=a; else out=b;
    }
    ping(ptr);
    pong(ptr);
    return out;
}
int neighbour(void) {return 9;}
'''


FLAGS = ('-O2', '-g3')


def choices(source=SOURCE, header=HEADER, flags=FLAGS, arch='mips'):
    return repairs.variants(source, 'demo', flags, header, arch)


def test_private_helper_scopes_and_call_line_bridge_are_bounded_and_reachable():
    out = choices()
    assert {family for family, _ in out} == set(repairs.FAMILIES)
    assert len(out) <= repairs.SITE_LIMIT*2+repairs.BRIDGE_LIMIT
    assert any('ping(ptr); pong(ptr);' in candidate and 'if (flag) {\n out=a;\n} else {\n out=b;\n}' in candidate for _, candidate in out)
    assert all(candidate.endswith('int neighbour(void) {return 9;}\n') for _, candidate in out)
    assert all('static inline s16 *__repair_byte_address(void *base, int offset)' in candidate for _, candidate in out)
    assert all(candidate.count('__repair_byte_address(ptr, 2)') == 1 for _, candidate in out)


@pytest.mark.parametrize('source', [
    SOURCE.replace('void *ptr', 'int ptr'),
    SOURCE.replace('void *ptr', 'volatile void *ptr'),
    SOURCE.replace('void *ptr', 'const void *ptr'),
    SOURCE.replace('void *ptr', 'Alias ptr'),
    SOURCE.replace('((u8 *)ptr + 2)', '((u8 *)next() + 2)'),
    SOURCE.replace('((u8 *)ptr + 2)', '((u8 *)ptr++ + 2)'),
    SOURCE.replace('((u8 *)ptr + 2)', '((u8 *)ptr + flag)'),
    SOURCE.replace('((u8 *)ptr + 2)', '((u8 *)ptr + 65536)'),
    SOURCE.replace('((u8 *)ptr + 2)', '((u8 *)ptr + -1)'),
    SOURCE.replace('s16 *)', 'Unknown *)'),
    SOURCE.replace('u8 *)', 'Unknown *)'),
    SOURCE.replace('int a;', 'int ptr; int a;'),
    SOURCE.replace('int a;', '{ int ptr; } int a;'),
    SOURCE.replace('int a;', 'int __repair_byte_address; int a;'),
    SOURCE.replace('int a;', '#line 99\nint a;'),
    SOURCE.replace('out=0;', 'out=__LINE__;'),
    SOURCE.replace('int a;', '#if FLAG\nint a;\n#endif\n'),
])
def test_unknown_effectful_shadowed_or_preprocessed_sites_give_no_proposal(source):
    assert choices(source) == []


@pytest.mark.parametrize('header', [
    HEADER.replace('signed short s16', 'volatile signed short s16'),
    HEADER.replace('unsigned char u8', 'const unsigned char u8'),
    HEADER+'\n#define ptr another\n',
    HEADER+'\n#define s16 short\n',
    HEADER+'\n#define unsigned volatile\n',
    HEADER+'\n#define offset secret()\n',
    HEADER+'\n#define inline\n',
    HEADER+'\n#line 99\n',
    HEADER+'\ntypedef int s16;\n',
])
def test_alias_qualifiers_conflicting_types_and_new_helper_macro_tokens_rejected(header):
    assert choices(header=header) == []


@pytest.mark.parametrize('arch', ['mips', 'ppc', 'x86', 'arm', 'thumb'])
def test_shared_supported_architectures(arch):
    assert choices(arch=arch)


def test_unknown_architecture_and_disabled_debug_bridges():
    assert choices(arch='unknown') == []
    out = choices(flags=('-O2', '-g0'))
    assert out and all(f == repairs.FAMILIES[0] for f, _ in out)


@pytest.mark.parametrize('body', [
    'if(flag) if(a) out=a; else out=b;',
    'if(flag) int local; else out=b;',
    'if(flag) label: out=a; else out=b;',
    'if(flag) out=a; /* preserve */ else out=b;',
    'if(flag) out=a; // preserve\nelse out=b;',
    'if(flag) out=a+1; else out=b;',
])
def test_scope_adjunct_never_reassociates_unknown_or_commented_arms(body):
    text = 'int demo(int flag,int a,int b){int out; '+body+' return out;}'
    out = repairs.branch_scopes(text, 'demo')
    if body.startswith('if(flag) if(a)'):
        # Only the inner complete if/else may be braced, preserving its association.
        assert len(out) == 1 and 'if(flag) if(a) {' in out[0]
    else:
        assert out == []


def test_call_pair_join_does_not_swallow_comments_directives_or_for_header():
    for text in (
        'int demo(void){ping(0); // comment\npong(0);}',
        'int demo(void){ping(0);\n#line 7\npong(0);}',
        'int demo(void){for(;ping(0);pong(0)) {}}',
        'int demo(void){ping(__LINE__);\npong(0);}',
    ):
        assert debug_boundary_repairs.call_pairs(text, 'demo', HEADER) == []


def test_octal_byte_displacement_keeps_c_value():
    out = choices(SOURCE.replace('+ 2)', '+ 010)'))
    assert out and '__repair_byte_address(ptr, 8)' in out[0][1]
    assert choices(SOURCE.replace('+ 2)', '+ 08)')) == []


def test_real_host_evaluation_and_effect_order_preserved(tmp_path):
    cc = (shutil.which('cc') or shutil.which('clang')) if os.name == 'nt' else (shutil.which('clang') or shutil.which('cc'))
    if not cc:
        pytest.skip('needs host compiler')
    code = HEADER+'\nint calls; int order;\nvoid ping(void *p){calls++;order=order*10+1;}\nvoid pong(void *p){calls++;order=order*10+2;}\n'
    original = SOURCE[:SOURCE.index('int neighbour')]
    code += original.replace('demo(', 'original(')
    names = ['original']
    for i, (_, candidate) in enumerate(choices()):
        candidate = candidate[:candidate.index('int neighbour')]
        candidate = candidate.replace('__repair_byte_address', f'address{i}').replace('demo(', f'candidate{i}(')
        code += candidate
        names.append(f'candidate{i}')
    path, libpath = tmp_path/'test.c', tmp_path/('test.dll' if os.name == 'nt' else 'test.so')
    path.write_text(code)
    subprocess.run([cc, '-shared', *(['-fPIC'] if os.name != 'nt' else []), '-std=c99',
                    str(path), '-o', str(libpath)], check=True)
    lib = ctypes.CDLL(str(libpath))
    for value in (-32768, -1, 0, 1, 32767):
        for flag in (0, 1):
            for name in names:
                fn = getattr(lib, name)
                fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
                data = (ctypes.c_short*4)(0, value, 0, 0)
                calls = ctypes.c_int.in_dll(lib, 'calls')
                order = ctypes.c_int.in_dll(lib, 'order')
                calls.value = order.value = 0
                expected = (1 if flag else 2) if value else 0
                assert fn(data, flag) == expected
                assert calls.value == 2 and order.value == 12
                assert tuple(data) == (0, value, 0, 0)


def test_no_generated_qualifiers_or_artificial_identity_operations():
    for _, candidate in choices():
        assert not re.search(r'\b(?:asm|__asm__|volatile|register)\b', candidate)
        assert 'offset + 0' not in candidate


def test_pinned_period_compiler_inlines_pure_address_without_extra_function(tmp_path):
    from io import BytesIO

    from elftools.elf.elffile import ELFFile

    from decompnet import toolchains
    from decompnet.compiler import compile_source
    from decompnet.elfobj import extract_function
    from decompnet.project import compiler_config
    if not toolchains.installed('gcc-2.7.2-kmc'):
        pytest.skip('needs pinned period compiler')
    cfg = compiler_config(tmp_path, {'compiler': {'toolchain': 'gcc-2.7.2-kmc',
                          'flags': ['-G0', '-mips3', '-mgp32', '-mfp32', '-O2', '-g3']}})
    text = 'int demo(void *ptr) {return *(s16 *)((u8 *)ptr + 2);}'
    control = compile_source(cfg, HEADER, text)
    assert control.ok, control.log
    out = choices(text)
    assert out
    for _, candidate in out:
        result = compile_source(cfg, HEADER, candidate)
        assert result.ok, result.log
        assert extract_function(result.obj, 'demo').code == extract_function(control.obj, 'demo').code
        elf = ELFFile(BytesIO(result.obj))
        functions = [s.name for s in elf.get_section_by_name('.symtab').iter_symbols() if s['st_info']['type']=='STT_FUNC']
        assert functions == ['demo']


@pytest.mark.parametrize('typ', ['long','unsigned long','signed char','unsigned char','short','unsigned short'])
def test_equal_width_does_not_change_primitive_pointer_kind(typ):
    text = SOURCE.replace('(s16 *)', '('+typ+' *)')
    out = choices(text)
    assert out
    assert all('static inline '+typ+' *__repair_byte_address' in candidate for _, candidate in out)
    assert all('return ('+typ+' *)' in candidate for _, candidate in out)


def test_plain_char_and_local_typedef_shadow_are_not_inferred():
    assert choices(SOURCE.replace('(s16 *)', '(char *)')) == []
    assert choices(SOURCE.replace('int a;', 'typedef int s16; int a;')) == []
    assert choices(SOURCE.replace('int a;', 'typedef short local; int a;').replace('(s16 *)','(local *)')) == []


def test_header_line_sensitive_macro_is_not_silently_changed_by_helper_insertion():
    assert choices(header=HEADER+'\nstatic int line = __LINE__;\n') == []


def test_bridge_budget_rotates_over_each_address_site_before_second_call_pair():
    text = '''int demo(void *p0,void *p1,void *p2,void *p3,int flag) {
    int out; int a; int b; a=1; b=2;
    out=*(s16 *)((u8 *)p0+2)+*(s16 *)((u8 *)p1+2)+*(s16 *)((u8 *)p2+2)+*(s16 *)((u8 *)p3+2);
    if(flag) out=a; else out=b;
    ping(p0);
    pong(p0);
    ping(p0);
    return out;
    }'''
    out = choices(text)
    bridges = [s for f,s in out if f == repairs.FAMILIES[2]]
    assert len(bridges) == repairs.BRIDGE_LIMIT
    roots = [re.search(r'__repair_byte_address\((p\d), 2\)',s)[1] for s in bridges]
    assert roots == ['p0','p1','p2','p3']*2
