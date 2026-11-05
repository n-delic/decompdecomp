"""Homemade entry-conversion controls; no game source or data."""
import ctypes
import re
import shutil
import subprocess

import pytest

from decompnet.app import promoted_entry_repairs as repair

HEADER = 'typedef signed char s8; typedef unsigned char u8; typedef short s16; typedef unsigned short u16; typedef int s32;'


def proposals(source, header=HEADER, **scope):
    return repair.variants(source, 'sample', header, language=scope.get('language', 'c'), int_bits=scope.get('int_bits', 32))


@pytest.mark.parametrize('typ', ['u8', 'unsigned char', 's8', 'signed char', 'u16', 'unsigned short', 's16', 'short'])
def test_real_entry_narrowing_keeps_integer_interface(typ):
    source = 'int sample(int x){int y;x=('+typ+')x;y=x+2;return y;}'
    result = proposals(source)
    assert len(result) == 2
    assert all(f == repair.FAMILY for f, _ in result)
    assert all(re.search(r'int sample\(int x_incoming\)', s) for _, s in result)
    assert all(typ+' x' in s for _, s in result)


@pytest.mark.parametrize('language,bits', [(None, None), ('c++',32), ('c',16), ('c',None)])
def test_unknown_language_or_promotion_width_stays_disabled(language,bits):
    assert proposals('int sample(int x){x=(u8)x;return x;}', language=language, int_bits=bits) == []


@pytest.mark.parametrize('tail', [
    'return sizeof(x);', 'return __alignof__(x);', 'return __alignof(x);',
    'return _Alignof(x);', 'return alignof(x);',
    '__auto_type y=x;y=256;return y;', '__typeof_unqual__(x) y;return x;',
    '__typeof__(x) y;return x;', 'typeof(x) y;return x;',
    'return _Generic(x,int:1,default:2);', 'return __builtin_types_compatible_p(int,int);',
    'auto y=x;return y;', 'return typeid(x);', 'decltype(x) y;return x;',
    'x+=1;return x;', 'x<<=1;return x;', '(x)>>=1;return x;',
    '++x;return x;', 'x++;return x;', 'return *( &x);', 'return *(&(x));',
    'int x=1;return x;', 'int *x;return 0;',
])
def test_observable_type_mutation_shadow_or_escape_is_not_rewritten(tail):
    assert proposals('int sample(int x){x=(u8)x;'+tail+'}') == []


@pytest.mark.parametrize('source', [
    'int sample(unsigned x){x=(u8)x;return x / -1;}',
    'int sample(int x){int y=x;x=(u8)x;return y+x;}',
    'int sample(int x,int c){if(c)goto after;x=(u8)x;after:return x;}',
    'int sample(int x,int c){if(c)x=(u8)x;return x;}',
    'int sample(int x){x=(u8)x;x=(u8)x;return x;}',
    'int sample(int x,...){x=(u8)x;return x;}',
    'int sample(volatile int x){x=(u8)x;return x;}',
])
def test_conversion_must_dominate_reads_with_compatible_unmodified_formal(source):
    assert proposals(source) == []


@pytest.mark.parametrize('header', [
    HEADER+'\n#define signed unsigned\n',
    HEADER+'\n#define int unsigned\n',
    HEADER+'\n#include "unknown.h"\n',
    HEADER+'\n#define OBSERVE(x) sizeof(x)\n',
])
def test_unresolved_headers_and_type_sensitive_macros_are_opaque(header):
    source='int sample(int x){x=(u8)x;return '+('OBSERVE(x)' if 'OBSERVE' in header else 'x')+';}'
    assert proposals(source,header) == []


def test_incoming_name_collision_uses_fresh_name():
    result = proposals('int sample(int x,int x_incoming){x=(u8)x;return x+x_incoming;}')
    assert result and all('int x_incoming_' in s for _,s in result)


def test_cpp_overload_is_disabled_by_compile_language():
    source='int g(int);int g(unsigned char);int sample(int x){x=(u8)x;return g(x);}'
    assert proposals(source, language='c++') == []
    assert repair.variants(source,'sample',HEADER) == []


def test_generated_c_agrees_with_independent_integer_conversion_oracle(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('host C compiler unavailable')
    cases = [('unsigned char',8,False),('signed char',8,True),('unsigned short',16,False),('short',16,True)]
    sources=[];functions=[]
    for i,(typ,bits,signed) in enumerate(cases):
        original='int sample(int x){int y;x=('+typ+')x;y=x*-2+3;return y;}'
        variants=[original]+[s for _,s in proposals(original)]
        for j,source in enumerate(variants):
            name=f'control_{i}_{j}'
            sources.append(re.sub(r'\bsample\b',name,source))
            functions.append((name,bits,signed))
    code=tmp_path/'controls.c';code.write_text('\n'.join(sources));library=tmp_path/'controls.so'
    subprocess.run([cc,'-std=c11','-shared','-fPIC','-O2',str(code),'-o',str(library)],check=True,capture_output=True)
    compiled=ctypes.CDLL(str(library))
    for name,bits,signed in functions:
        function=getattr(compiled,name);function.argtypes=[ctypes.c_int];function.restype=ctypes.c_int
        for value in [-2147483648,-65537,-32769,-257,-129,-1,0,1,127,128,255,256,32767,32768,65535,65536,2147483647]:
            narrow=value&((1<<bits)-1)
            if signed and narrow&(1<<(bits-1)):
                narrow-=1<<bits
            assert function(value)==narrow*-2+3,(name,value)


def test_direct_pinned_c_profile_scope_and_unknown_flags():
    profile = {'toolchain':'gcc-2.7.2-kmc','flags':['-G8','-mips3','-mgp32','-mfp32','-O2','-ffast-math','-fno-strength-reduce','-g3']}
    assert repair.profile_scope('mips', profile) == {'language':'c','int_bits':32}
    assert repair.profile_scope('arm', profile) == {}
    for flag in ['-x','c++','-Dsizeof(x)=0','-include','-fshort-int','@options','-mabi=unknown']:
        assert repair.profile_scope('mips', profile | {'flags':[flag]}) == {}
    assert repair.profile_scope('mips', profile | {'cmd':['custom','{src}']}) == {}
    assert repair.profile_scope('mips', {'toolchain':'unknown'}) == {}


def test_external_build_scope_requires_invocation_evidence():
    from types import SimpleNamespace
    net = SimpleNamespace(fork_slug=lambda slug:True)
    assert repair.scope(net, {'slug':'sample'}, {}) == {}


def test_shared_generator_routes_explicit_scope_only():
    from decompnet.app import draft_repairs
    source = 'int sample(int x){x=(u8)x;return x+2;}'
    labels = {}
    draft_repairs.variants(source,'sample','mips',header=HEADER,labels=labels,limit=None)
    assert repair.FAMILY not in labels.values()
    labels = {}
    result = draft_repairs.variants(source,'sample','mips',header=HEADER,labels=labels,limit=None,
                                    entry_scope={'language':'c','int_bits':32})
    assert len([s for s in result if labels[s] == repair.FAMILY]) == 2
    assert repair.FAMILY in draft_repairs.AGENT_FAMILIES


def test_conditional_type_contract_is_not_guessed():
    header = '#if CHOICE\ntypedef unsigned char byte;\n#else\ntypedef unsigned int byte;\n#endif'
    assert proposals('int sample(int x){x=(byte)x;return x;}', header) == []


def test_iterative_cache_distinguishes_proven_language_scope():
    from decompnet.app import iterative_repairs
    source = 'int sample(int x){x=(u8)x;return x+2;}'
    cache = {}
    args = (source, {'rows':[]}, {'name':'sample','asm':''}, 'mips', [])
    unknown = iterative_repairs.targeted_variants(*args,header=HEADER,tagged=True,limit=999,pool_cache=cache)
    proven = iterative_repairs.targeted_variants(*args,header=HEADER,tagged=True,limit=999,pool_cache=cache,
                                                entry_scope={'language':'c','int_bits':32})
    assert not any(f == repair.FAMILY for f, _ in unknown)
    assert any(f == repair.FAMILY for f, _ in proven)
    assert len(cache) == 2


@pytest.mark.parametrize('source', [
    '#de\\\nfine OBS(x) __align\\\nof__(x)\nint sample(int x){x=(unsigned char)x;return OBS(x);}',
    '#define S(x) __align\\\nof__(x)\n#define O\\\nBS S\nint sample(int x){x=(unsigned char)x;return OBS(x);}',
    '#de??/\nfine OBS(x) __alignof__(x)\nint sample(int x){x=(unsigned char)x;return OBS(x);}',
])
def test_preprocessing_line_splices_cannot_hide_type_observers(source):
    assert proposals(source) == []


def test_registry_command_and_prelude_are_part_of_scope_proof(monkeypatch):
    from dataclasses import replace

    from decompnet import toolchains
    original = toolchains.get('gcc-2.7.2-kmc')
    for changed in [replace(original,cc=[*original.cc,'-x','c++']),
                    replace(original,prelude='#define sizeof(x) 1')]:
        monkeypatch.setattr(toolchains,'get',lambda tid, changed=changed:changed)
        assert repair.profile_scope('mips',{'toolchain':original.id}) == {}


@pytest.mark.parametrize('statement', ['y=x=(u8)x;', 'return x=(u8)x;', '(x=(u8)x);', 'y=0,x=(u8)x;'])
def test_narrowing_must_be_an_entire_expression_statement(statement):
    source = 'int g(void);int sample(int x){int y;'+statement+'g();return y;}'
    assert proposals(source) == []


@pytest.mark.parametrize('directive', ['#import "hidden.h"', '#include_next "hidden.h"', '#line 1 "hidden.c"'])
def test_unknown_directives_do_not_hide_type_contracts(directive):
    assert proposals(directive+'\nint sample(int x){x=(u8)x;return observer(x);}') == []
