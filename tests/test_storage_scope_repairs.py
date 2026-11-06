"""Coupled storage/scope hypotheses remain bounded and compiler-setting gated."""
import pytest

from decompnet.app import draft_repairs, storage_scope_repairs

SOURCE = '''int f(void *base, int choice) {
void *p;
int x;
if (choice) {
p=*(void**)((char*)base+8);
do { x=*(int*)((char*)p+4); } while(0);
} else {
p=*(void**)((char*)base+12);
choice=choice+1;
x=*(int*)((char*)p+4);
}
return x;
}'''


def test_bundle_changes_storage_and_scope_together():
    out = storage_scope_repairs.variants(SOURCE, 'f', 'mips', ['-g3'])
    assert len(out) == 1
    assert 'int __repair_pointer_bits;' in out[0]
    assert '__repair_pointer_bits=*(int*)((char*)base+8);' in out[0]
    assert 'x=*(int*)((char*)__repair_pointer_bits+4);' in out[0]
    assert 'p=*(void**)((char*)base+12);\ndo {' in out[0]
    assert out[0].count('while(0);') == 2
    labels = {}
    assert out[0] in draft_repairs.variants(SOURCE, 'f', 'mips', flags=['-g3'], labels=labels)
    assert labels[out[0]] == 'pointer_storage_scope'


@pytest.mark.parametrize('arch,flags', [('mips', []), ('mips', ['-g0']), ('x86', ['-g']), ('unknown', ['-g3'])])
def test_only_debug_mips(arch, flags):
    assert not storage_scope_repairs.variants(SOURCE, 'f', arch, flags)


@pytest.mark.parametrize('old,new', [
    ('void *p;', 'void *volatile p;'),
    ('choice=choice+1;', 'send(choice);'),
    ('choice=choice+1;', 'choice++;'),
    ('choice=choice+1;', 'int choice;'),
    ('p=*(void**)((char*)base+8);', 'p=*(void**)next();'),
    ('x=*(int*)((char*)p+4); }', 'send(p); }'),
    ('return x;', 'return *(int*)p;'),
    ('choice=choice+1;', 'choice=sizeof("p");'),
    ('int x;', '#define SOMETHING 1\nint x;'),
])
def test_unsafe_or_unrecognised_shapes_are_skipped(old, new):
    assert not storage_scope_repairs.variants(SOURCE.replace(old, new), 'f', 'mips', ['-g3'])


def test_neighbour_and_strings_are_opaque():
    source = 'void before(){} ' + SOURCE + ' void after(){}'
    out = storage_scope_repairs.variants(source, 'f', 'mips', ['-g3'])
    assert out and out[0].startswith('void before(){} ') and out[0].endswith(' void after(){}')
    assert not storage_scope_repairs.variants('void f(){say("if(x){p=*(void**)a;}");}', 'f', 'mips', ['-g3'])
