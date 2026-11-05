"""Pure local preheader scheduling: semantic controls and fail-closed inputs."""
import ctypes
import shutil
import subprocess

import pytest

from decompnet.app import preheader_order_repairs as repair

BASE = 'typedef void (*Hook)(void);void cb(void);int sample(int v){int a;int b;Hook hook;a=v+3;hook=cb;b=1;again:return a+b+(hook==cb);}'


def test_independent_callback_integer_preheader_is_bounded():
    result = repair.variants(BASE,'sample')
    assert len(result) == 5
    assert all(family == repair.FAMILY for family,_ in result)
    assert any('hook=cb;\nb=1;\na=v+3;' in s for _,s in result)
    assert all(s.endswith('again:return a+b+(hook==cb);}') for _,s in result)


@pytest.mark.parametrize('source', [
    BASE.replace('b=1;','b=a+1;'),
    BASE.replace('a=v+3;','a=fetch();'),
    BASE.replace('a=v+3;','a=*p;'),
    BASE.replace('a=v+3;','a=p[0];'),
    BASE.replace('a=v+3;','a=v++;'),
    BASE.replace('int a;','volatile int a;'),
    BASE.replace('int a;','extern int a;'),
    BASE.replace('int a;','int a[1];'),
    BASE.replace('again:', 'touch();again:'),
    BASE.replace('int a;', 'struct X{int a;};'),
    '#define cb hidden()\n'+BASE,
    '#include "hidden.h"\n'+BASE,
    '#define Hook volatile int\n'+BASE,
    '#define QUAL volatile\n'+BASE.replace('int a;','QUAL int a;'),
    BASE.replace('a=v+3;', 'a=(int)\"literal\";'),
    BASE.replace('again:', 'if(v) a=3;again:'),
])
def test_unsupported_memory_effect_dependency_or_scope_is_not_reordered(source):
    assert repair.variants(source,'sample') == []


def test_hidden_volatile_integer_alias_and_conditional_types_are_not_guessed():
    source = BASE.replace('int a;','u32 a;')
    assert not repair.variants(source,'sample','typedef volatile unsigned int u32;')
    assert not repair.variants(source,'sample')
    assert repair.variants(source,'sample','typedef unsigned int u32;')
    assert not repair.variants(source,'sample','#if MODE\ntypedef unsigned int u32;\n#endif')


def test_identifier_digits_are_not_mistaken_for_integer_literals():
    source = BASE.replace('cb','callback_1234').replace('int v','int value7').replace('a=v+3','a=value7+3')
    assert repair.variants(source,'sample')


def test_native_effects_values_and_callback_identity(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('native C compiler unavailable')
    source = BASE.replace('void cb(void);','int hits;void cb(void){hits++;}int seen(void){return hits;}')
    source = source.replace('again:return','again:hook();return')
    for i,text in enumerate([source]+[s for _,s in repair.variants(source,'sample')]):
        file,lib = tmp_path/f'{i}.c',tmp_path/f'{i}.so'
        file.write_text(text)
        subprocess.run([cc,'-shared','-fPIC','-O2',str(file),'-o',str(lib)],check=True,capture_output=True,timeout=20)
        native = ctypes.CDLL(str(lib))
        for x in [-100,0,100]:
            before = native.seen()
            assert native.sample(x) == x+5
            assert native.seen() == before+1


def test_shared_generator_routes_the_family():
    from decompnet.app import draft_repairs
    expected = {s for _,s in repair.variants(BASE,'sample')}
    assert repair.FAMILY in draft_repairs.AGENT_FAMILIES
    assert expected <= set(draft_repairs.variants(BASE,'sample','mips',limit=100))
