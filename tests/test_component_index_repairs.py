"""Real component capture, with initialization dominance and macro boundaries."""
import ctypes
import shutil
import subprocess

import pytest

from decomprepair import component_index_repairs as repair

BASE = '#define GET(p,i) ((p)[i])\nint sample(int *p){int value;int d[3];value=0;d[1]=GET(p,1);d[2]=GET(p,2);value=d[1]+d[2];return value;}'


def proposals(source=BASE, header=''):
    return repair.variants(source,'sample',header,language='c',int_bits=32)


def test_capture_is_consumed_by_real_macro_and_array_reads():
    result = proposals()
    assert result
    assert len(result) <= repair.LIMIT
    for family,source in result:
        assert family == repair.FAMILY
        assert source.count('__repair_component_index') == 4
        assert 'int __repair_component_index;' in source
        assert '__repair_component_index = ' in source


@pytest.mark.parametrize('body', [
    'if(p[0]) d[1]=GET(p,1);value=d[1];',
    'while(p[0]) d[1]=GET(p,1);value=d[1];',
    'if(p[0]) {d[1]=GET(p,1);}value=d[1];',
])
def test_initialization_cannot_be_bypassed(body):
    assert not proposals('#define GET(p,i) ((p)[i])\nint sample(int*p){int value;int d[3];value=0;'+body+'return value;}')


@pytest.mark.parametrize('prefix', [
    '#define int volatile int\n', '#include "hidden.h"\n',
    '#define GET(p,i) ((p)[i])\n',
])
def test_hidden_types_includes_or_redefinition_fail_closed(prefix):
    assert not proposals(prefix+BASE)


@pytest.mark.parametrize('definition', [
    '#define GET(p,i) if(i) p[i]',
    '#define GET(p,i) ({p[i];})',
    '#define GET(p,i) __builtin_constant_p(i)',
    '#define GET(p,i) sizeof(i)',
    '#define GET(p,i) #i',
    '#define GET(p,i) (',
    '#define GET(p,i) (p[i]) )',
])
def test_control_scope_introspection_and_unbalanced_macros_are_not_guessed(definition):
    assert not proposals(BASE.replace('#define GET(p,i) ((p)[i])',definition))


def test_goto_line_observers_and_unknown_invocation_are_excluded():
    assert not proposals(BASE.replace('return value;','goto end;end:return value;'))
    assert not proposals(BASE.replace('return value;','return value+__LINE__;'))
    assert not repair.variants(BASE,'sample')
    assert not repair.variants(BASE,'sample',language='c++',int_bits=32)


def test_unrelated_header_sizeof_does_not_hide_safe_component_reads():
    assert proposals(BASE,'typedef char SizeCheck[sizeof(int)==4?1:-1];')


def test_existing_names_are_not_shadowed():
    source = BASE.replace('int value;','int __repair_component_index;int value;')
    result = proposals(source)
    assert result and all('int __repair_component_index_;' in text for _,text in result)


def test_literal_types_and_indices_are_not_widened_or_invented():
    assert not proposals(BASE.replace('GET(p,1)','GET(p,1U)').replace('GET(p,2)','GET(p,2L)'))
    assert not proposals(BASE.replace('GET(p,1)','GET(p,3)').replace('GET(p,2)','GET(p,3)'))


def test_native_values_and_output_effects_are_preserved(tmp_path):
    cc = shutil.which('cc')
    if not cc:
        pytest.skip('native C compiler unavailable')
    for i,text in enumerate([BASE]+[s for _,s in proposals()]):
        file,lib = tmp_path/f'{i}.c',tmp_path/f'{i}.so'
        file.write_text(text)
        subprocess.run([cc,'-shared','-fPIC','-O2',str(file),'-o',str(lib)],check=True,capture_output=True,timeout=20)
        function = ctypes.CDLL(str(lib)).sample
        function.argtypes = [ctypes.POINTER(ctypes.c_int)]
        for values in [(0,1,2),(7,-100,99),(9,32767,-32768)]:
            data = (ctypes.c_int*3)(*values)
            assert function(data) == values[1]+values[2]
            assert tuple(data) == values


def test_shared_generator_routes_only_with_invocation_scope():
    from decompnet.app import draft_repairs
    assert repair.FAMILY in draft_repairs.AGENT_FAMILIES
    assert {s for _,s in proposals()} <= set(draft_repairs.variants(BASE,'sample','mips',limit=100,entry_scope={'language':'c','int_bits':32}))
