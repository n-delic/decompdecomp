"""Pointer staging reuses only a spare local and keeps the original load in place."""
import pytest

from decompnet.app import temporary_repairs

SOURCE='void f(){void *spare;u8 *p;p=*((void **)(base+4));send(p);spare=next();use(spare);}'


def test_pointer_staging_uses_existing_spare_before_its_next_assignment():
    out=temporary_repairs.variants(SOURCE,'f')
    assert len(out)==1
    assert 'spare = *((void **)(base+4)); p=(u8 *)spare;send(p);spare=next();' in out[0]
    assert out[0].count('*((void **)(base+4))')==1
    assert out[0].count('void *spare;')==1


@pytest.mark.parametrize('old,new',[
    ('void *spare;','void *volatile spare;'),
    ('void *spare;','void **spare;'),
    ('u8 *p;','u8 *volatile p;'),
    ('p=*((void **)(base+4));','p=*((void **)next());'),
    ('p=*((void **)(base+4));','p=*((void **)(base++));'),
    ('send(p);','use(spare);'),
    ('spare=next();','spare=spare;'),
    ('spare=next();','spare+=4;'),
    ('spare=next();','spare==next();'),
    ('p=*((void **)(base+4));','spare=base;p=*((void **)(base+4));'),
    ('p=*((void **)(base+4));','save(&spare);p=*((void **)(base+4));')])
def test_pointer_staging_rejects_live_escaped_qualified_and_side_effecting_cases(old,new):
    assert not temporary_repairs.variants(SOURCE.replace(old,new),'f')


def test_pointer_staging_stays_inside_the_requested_definition():
    source=SOURCE.replace('void f()','void g()')+' '+SOURCE
    out=temporary_repairs.variants(source,'f')
    assert out and all(c.startswith(source[:source.index(' void f()')]) for c in out)
    assert not temporary_repairs.variants('void f(){say("void *spare;u8 *p;p=*((void **)(base+4));");}','f')


def test_neighbouring_formal_is_not_a_parameter_of_the_target_function():
    source='void g(int value){} void f(){int value;int spare;value=a*b;}'
    assert not temporary_repairs.variants(source,'f')
